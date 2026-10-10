from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct

from .config import FRP_TOKEN_PARAM

_USER_DATA_TEMPLATE = Path(__file__).parent / "assets" / "user_data.sh"
_SERVER_SETUP_SCRIPT = (
    Path(__file__).parent.parent.parent / "server" / "assets" / "server-setup.sh"
)


class TunnelStack(Stack):
    """EC2 host running frps + Caddy, reachable at a wildcard subdomain of domain_name.

    DNS is managed externally (e.g. Cloudflare): point a wildcard record
    (``*.domain_name``) at the stack's Elastic IP. Caddy obtains TLS certs
    on demand via Let's Encrypt HTTP-01, so no Route53 hosted zone is needed.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        frp_token: str,
        vpc: ec2.IVpc | None = None,
        frp_bind_port: int = 7000,
        frp_vhost_http_port: int = 7080,
        token_param_name: str = FRP_TOKEN_PARAM,
        frp_version: str = "0.71.0",
        caddy_version: str = "2.9.1",
        extra_port_range: tuple[int, int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # `from_lookup` requires a Stack scope, so it's resolved here (self)
        # rather than in the caller. Tests inject a fake VPC instead of
        # exercising this live lookup.
        vpc = vpc or ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        token_param_arn = self.format_arn(
            service="ssm",
            resource="parameter",
            resource_name=token_param_name.lstrip("/"),
        )
        # AWS::SSM::Parameter does not support Type: SecureString in
        # CloudFormation (String/StringList only), so this has to go
        # through a custom resource calling the SDK directly.
        put_parameter_call = cr.AwsSdkCall(
            service="SSM",
            action="PutParameter",
            parameters={
                "Name": token_param_name,
                "Type": "SecureString",
                "Value": frp_token,
                "Overwrite": True,
            },
            physical_resource_id=cr.PhysicalResourceId.of(token_param_name),
        )
        token_param = cr.AwsCustomResource(
            self,
            "FrpTokenParameter",
            on_create=put_parameter_call,
            on_update=put_parameter_call,
            on_delete=cr.AwsSdkCall(
                service="SSM",
                action="DeleteParameter",
                parameters={"Name": token_param_name},
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=[token_param_arn]
            ),
        )

        security_group = ec2.SecurityGroup(
            self,
            "TunnelSecurityGroup",
            vpc=vpc,
            description="wildtunnel EC2 host",
            allow_all_outbound=True,
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP (redirects to HTTPS)"
        )
        security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(frp_bind_port), "frp control channel"
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.icmp_ping(), "ping (ICMP echo)"
        )
        if extra_port_range is not None:
            start, end = extra_port_range
            security_group.add_ingress_rule(
                ec2.Peer.any_ipv4(),
                ec2.Port.tcp_range(start, end),
                "extra tcp tunnels (e.g. WireGuard, Sunshine)",
            )
            security_group.add_ingress_rule(
                ec2.Peer.any_ipv4(),
                ec2.Port.udp_range(start, end),
                "extra udp tunnels (e.g. WireGuard, Sunshine)",
            )

        role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"], resources=[token_param_arn]
            )
        )

        allow_ports = (
            f"allowPorts = [{{ start = {extra_port_range[0]}, "
            f"end = {extra_port_range[1]} }}]"
            if extra_port_range is not None
            else ""
        )

        user_data = ec2.UserData.for_linux()
        script = _USER_DATA_TEMPLATE.read_text()
        for placeholder, value in {
            "__DOMAIN__": domain_name,
            "__TOKEN_PARAM__": token_param_name,
            "__BIND_PORT__": str(frp_bind_port),
            "__VHOST_HTTP_PORT__": str(frp_vhost_http_port),
            "__REGION__": self.region,
            "__FRP_VERSION__": frp_version,
            "__CADDY_VERSION__": caddy_version,
            "__ALLOW_PORTS__": allow_ports,
            "__SERVER_SETUP_SCRIPT__": _SERVER_SETUP_SCRIPT.read_text(),
        }.items():
            script = script.replace(placeholder, value)
        user_data.add_commands(script)

        instance = ec2.Instance(
            self,
            "TunnelInstance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T4G, ec2.InstanceSize.NANO
            ),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64
            ),
            security_group=security_group,
            role=role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        8, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )
        # UserData changes don't force CloudFormation to replace an
        # AWS::EC2::Instance (it tries to stop/modify/start in place, which
        # also doesn't re-run cloud-init). When a boot script needs to be
        # re-run on a fresh instance, bump this logical ID to force a real
        # replacement instead of updating an instance that no longer exists.
        instance.instance.override_logical_id("TunnelInstanceV4")
        # frps reads the token param at boot; make sure it exists first.
        instance.node.add_dependency(token_param)

        eip = ec2.CfnEIP(
            self, "TunnelEip", domain="vpc", instance_id=instance.instance_id
        )

        CfnOutput(self, "ElasticIp", value=eip.ref)
        CfnOutput(self, "InstanceId", value=instance.instance_id)
