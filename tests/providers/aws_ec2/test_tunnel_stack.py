from typing import Any

from aws_cdk import App, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk.assertions import Match, Template

from wildtunnel.providers.aws_ec2.tunnel_stack import TunnelStack


def _build_template(**kwargs: Any) -> Template:
    app = App()
    fixtures = Stack(app, "Fixtures")
    vpc = ec2.Vpc.from_vpc_attributes(
        fixtures,
        "Vpc",
        vpc_id="vpc-12345",
        availability_zones=["ap-northeast-1a"],
        public_subnet_ids=["subnet-12345"],
    )

    stack = TunnelStack(
        app,
        "TunnelStack",
        vpc=vpc,
        domain_name="dasbd72.com",
        frp_token="test-token",
        **kwargs,
    )
    return Template.from_stack(stack)


def test_security_group_allows_http_https_and_frp_control_port() -> None:
    template = _build_template()

    for port in (80, 443, 7000):
        template.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "SecurityGroupIngress": Match.array_with(
                    [
                        Match.object_like(
                            {"CidrIp": "0.0.0.0/0", "FromPort": port, "ToPort": port}
                        )
                    ]
                )
            },
        )


def test_security_group_allows_ping() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::EC2::SecurityGroup",
        {
            "SecurityGroupIngress": Match.array_with(
                [
                    Match.object_like(
                        {
                            "CidrIp": "0.0.0.0/0",
                            "IpProtocol": "icmp",
                            "FromPort": 8,
                            "ToPort": -1,
                        }
                    )
                ]
            )
        },
    )


def test_instance_is_t4g_nano_arm() -> None:
    template = _build_template()

    template.has_resource_properties("AWS::EC2::Instance", {"InstanceType": "t4g.nano"})


def test_instance_role_grants_ssm_managed_instance_core() -> None:
    template = _build_template()

    template_json = template.to_json()
    role_resources = [
        resource
        for resource in template_json["Resources"].values()
        if resource["Type"] == "AWS::IAM::Role"
    ]
    assert any("AmazonSSMManagedInstanceCore" in str(role) for role in role_resources)


def test_instance_role_has_no_route53_permissions() -> None:
    # DNS is managed externally now; the host never touches Route53.
    template = _build_template()

    assert "route53" not in str(template.to_json()).lower()


def test_instance_role_can_read_frp_token_parameter() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "ssm:GetParameter",
                                "Effect": "Allow",
                            }
                        )
                    ]
                )
            }
        },
    )


def test_frp_token_stored_as_secure_string_parameter() -> None:
    # AWS::SSM::Parameter doesn't support Type: SecureString in
    # CloudFormation, so this goes through a Custom::AWS resource that calls
    # ssm:PutParameter directly instead.
    template = _build_template()

    template.has_resource_properties(
        "Custom::AWS",
        {
            "Create": Match.serialized_json(
                Match.object_like(
                    {
                        "service": "SSM",
                        "action": "PutParameter",
                        "parameters": Match.object_like(
                            {
                                "Name": "/reverse-tunnel/frp-token",
                                "Type": "SecureString",
                                "Value": "test-token",
                            }
                        ),
                    }
                )
            )
        },
    )


def test_elastic_ip_is_allocated_and_associated_to_instance() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::EC2::EIP",
        {"Domain": "vpc", "InstanceId": Match.any_value()},
    )


def test_no_dns_record_is_managed() -> None:
    # DNS lives on an external provider (e.g. Cloudflare); the stack must not
    # create any Route53 records of its own.
    template = _build_template()

    assert template.find_resources("AWS::Route53::RecordSet") == {}


def test_no_extra_port_range_opened_by_default() -> None:
    template = _build_template()

    template.has_resource_properties(
        "AWS::EC2::SecurityGroup",
        {
            "SecurityGroupIngress": Match.not_(
                Match.array_with([Match.object_like({"FromPort": 20000})])
            )
        },
    )


def _user_data(template: Template) -> str:
    template_json = template.to_json()
    (launch_template,) = [
        resource
        for resource in template_json["Resources"].values()
        if resource["Type"] == "AWS::EC2::Instance"
    ]
    user_data_b64 = launch_template["Properties"]["UserData"]["Fn::Base64"]
    # Fn::Base64 wraps an Fn::Join whose parts include the literal script
    # text; stringifying is enough to search it without re-implementing the
    # intrinsic-function evaluator.
    return str(user_data_b64)


def test_user_data_wrapper_fetches_token_from_ssm() -> None:
    template = _build_template()

    user_data = _user_data(template)
    assert "aws ssm get-parameter" in user_data
    assert "/reverse-tunnel/frp-token" in user_data


def test_user_data_wrapper_exports_full_env_var_contract() -> None:
    template = _build_template()

    user_data = _user_data(template)
    for var in (
        "DOMAIN",
        "FRP_TOKEN",
        "FRP_VERSION",
        "CADDY_VERSION",
        "FRP_BIND_PORT",
        "FRP_VHOST_HTTP_PORT",
        "ALLOW_PORTS",
    ):
        assert f"export {var}=" in user_data, f"missing export of {var}"


def test_user_data_wrapper_invokes_generic_installer_script() -> None:
    template = _build_template()

    user_data = _user_data(template)
    assert "/tmp/server-setup.sh" in user_data
    # The generic installer's own content (e.g. the swap-file setup that
    # has nothing to do with AWS) must be embedded verbatim, not re-derived.
    assert "mkswap /swapfile" in user_data


def test_security_group_opens_extra_tcp_and_udp_port_range_when_configured() -> None:
    template = _build_template(extra_port_range=(20000, 20100))

    for protocol in ("tcp", "udp"):
        template.has_resource_properties(
            "AWS::EC2::SecurityGroup",
            {
                "SecurityGroupIngress": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "CidrIp": "0.0.0.0/0",
                                "IpProtocol": protocol,
                                "FromPort": 20000,
                                "ToPort": 20100,
                            }
                        )
                    ]
                )
            },
        )
