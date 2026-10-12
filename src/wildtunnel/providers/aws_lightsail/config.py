from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import boto3

from ...frpc_config import DEFAULT_SERVER_PORT

FRP_VERSION = "0.71.0"
CADDY_VERSION = "2.9.1"
FRP_VHOST_HTTP_PORT = 7080

_INSTANCE_RUNNING_POLL_INTERVAL_SECS = 5
_INSTANCE_RUNNING_MAX_ATTEMPTS = 24  # ~2 minutes

_USER_DATA_TEMPLATE = Path(__file__).parent / "assets" / "user_data.sh"
_SERVER_SETUP_SCRIPT = (
    Path(__file__).parent.parent.parent / "server" / "assets" / "server-setup.sh"
)

# Lightsail instances have no attachable IAM role (unlike EC2's instance
# profile), so there's no SSM-token-fetch dance for this provider - the
# frp token is baked directly into user data at deploy time instead. One
# consequence: `info` (and anything else needing the token) only works on
# the machine that ran `deploy`, since the token is never stored remotely.
PROVIDER = "aws-lightsail"
INSTANCE_NAME = "wildtunnel"
STATIC_IP_NAME = "wildtunnel-ip"
DEFAULT_BLUEPRINT_ID = "amazon_linux_2023"
DEFAULT_BUNDLE_ID = "nano_3_0"


@dataclasses.dataclass
class AwsLightsailConfig:
    # domain/extra_port_range are provider-agnostic and sit top-level on
    # disk; region is aws_lightsail-only and sits nested under
    # "aws_lightsail" (see save()/load()) - mirrors AwsEc2Config's split.
    domain: str
    region: str
    extra_port_range: tuple[int, int] | None = None
    no_tls: bool = False

    @classmethod
    def load(cls, path: Path) -> AwsLightsailConfig:
        data = json.loads(path.read_text())
        extra_port_range = data.get("extra_port_range")
        return cls(
            domain=data["domain"],
            region=data["aws_lightsail"]["region"],
            extra_port_range=(
                (extra_port_range[0], extra_port_range[1])
                if extra_port_range is not None
                else None
            ),
            no_tls=data.get("no_tls", False),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "provider": PROVIDER,
            "domain": self.domain,
            "extra_port_range": (
                list(self.extra_port_range) if self.extra_port_range else None
            ),
            "no_tls": self.no_tls,
            "aws_lightsail": {"region": self.region},
        }
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def availability_zone(region: str) -> str:
    return f"{region}a"


def port_infos(
    frp_bind_port: int,
    extra_port_range: tuple[int, int] | None,
    ssh_cidr: str | None = None,
) -> list[dict[str, Any]]:
    """Build the full desired PutInstancePublicPorts portInfos list.

    PutInstancePublicPorts replaces the whole firewall rule set
    atomically, so this always returns the complete set rather than a
    diff - there's nothing to merge with what's already open.

    SSH/22 is always scoped to the "lightsail-connect" cidrListAliases
    entry, which Lightsail resolves to its own browser-based SSH
    client's IPs - the browser console requires this rule to exist
    (it isn't exempt from the firewall), but it's never opened to the
    public internet by default. Pass ssh_cidr to additionally allow a
    specific CIDR on the same port, for a real SSH client.
    """
    infos: list[dict[str, Any]] = [
        {"fromPort": 80, "toPort": 80, "protocol": "tcp", "cidrs": ["0.0.0.0/0"]},
        {"fromPort": 443, "toPort": 443, "protocol": "tcp", "cidrs": ["0.0.0.0/0"]},
        {
            "fromPort": frp_bind_port,
            "toPort": frp_bind_port,
            "protocol": "tcp",
            "cidrs": ["0.0.0.0/0"],
        },
        {"fromPort": -1, "toPort": -1, "protocol": "icmp", "cidrs": ["0.0.0.0/0"]},
        {
            "fromPort": 22,
            "toPort": 22,
            "protocol": "tcp",
            "cidrs": [ssh_cidr] if ssh_cidr is not None else [],
            "cidrListAliases": ["lightsail-connect"],
        },
    ]
    if extra_port_range is not None:
        start, end = extra_port_range
        infos.append(
            {
                "fromPort": start,
                "toPort": end,
                "protocol": "tcp",
                "cidrs": ["0.0.0.0/0"],
            }
        )
        infos.append(
            {
                "fromPort": start,
                "toPort": end,
                "protocol": "udp",
                "cidrs": ["0.0.0.0/0"],
            }
        )
    return infos


def render_user_data(
    *,
    domain: str,
    frp_token: str,
    frp_bind_port: int,
    frp_vhost_http_port: int,
    frp_version: str,
    caddy_version: str,
    extra_port_range: tuple[int, int] | None,
    frp_stats_user: str = "",
    frp_stats_password: str = "",
    dashboard_admin_password: str = "",
    no_tls: bool = False,
) -> str:
    allow_ports = (
        f"allowPorts = [{{ start = {extra_port_range[0]}, "
        f"end = {extra_port_range[1]} }}]"
        if extra_port_range is not None
        else ""
    )
    script = _USER_DATA_TEMPLATE.read_text()
    for placeholder, value in {
        "__DOMAIN__": domain,
        "__FRP_TOKEN__": frp_token,
        "__BIND_PORT__": str(frp_bind_port),
        "__VHOST_HTTP_PORT__": str(frp_vhost_http_port),
        "__FRP_VERSION__": frp_version,
        "__CADDY_VERSION__": caddy_version,
        "__ALLOW_PORTS__": allow_ports,
        "__FRP_STATS_USER__": frp_stats_user,
        "__FRP_STATS_PASSWORD__": frp_stats_password,
        "__DASHBOARD_ADMIN_PASSWORD__": dashboard_admin_password,
        "__NO_TLS__": "1" if no_tls else "",
        "__SERVER_SETUP_SCRIPT__": _SERVER_SETUP_SCRIPT.read_text(),
    }.items():
        script = script.replace(placeholder, value)
    return script


def describe_instance(region: str) -> dict[str, Any] | None:
    """Fetch the deployed instance's description, or None if it doesn't exist."""
    client = boto3.client("lightsail", region_name=region)
    try:
        instance: dict[str, Any] = client.get_instance(instanceName=INSTANCE_NAME)[
            "instance"
        ]
        return instance
    except client.exceptions.NotFoundException:
        return None


def describe_port_states(region: str) -> list[dict[str, Any]] | None:
    """Fetch the instance's current firewall port rules, or None if it doesn't exist."""
    client = boto3.client("lightsail", region_name=region)
    try:
        states: list[dict[str, Any]] = client.get_instance_port_states(
            instanceName=INSTANCE_NAME
        )["portStates"]
        return states
    except client.exceptions.NotFoundException:
        return None


def _port_key(entry: dict[str, Any]) -> tuple[int, int, str]:
    return (entry["fromPort"], entry["toPort"], entry["protocol"])


def diff_ports(
    desired: list[dict[str, Any]], current: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare the desired portInfos against the instance's current port states.

    Returns (to_open, to_close): desired rules not currently open, and
    currently-open rules that deploy would no longer set (e.g. after
    shrinking or removing --extra-port-range, or changing --ssh-cidr).
    Only fromPort/toPort/protocol are compared, not cidrs - so a changed
    --ssh-cidr value on an already-open SSH rule won't show as drift
    here, even though `deploy` would still apply the new cidr.
    """
    open_keys = {_port_key(entry) for entry in current if entry.get("state") == "open"}
    desired_keys = {_port_key(entry) for entry in desired}

    to_open = [entry for entry in desired if _port_key(entry) not in open_keys]
    to_close = [
        entry for entry in current if _port_key(entry) in open_keys - desired_keys
    ]
    return to_open, to_close


def _wait_until_running(client: Any) -> None:
    for _attempt in range(_INSTANCE_RUNNING_MAX_ATTEMPTS):
        instance = client.get_instance(instanceName=INSTANCE_NAME)["instance"]
        if instance["state"]["name"] == "running":
            return
        time.sleep(_INSTANCE_RUNNING_POLL_INTERVAL_SECS)
    raise TimeoutError(
        f"instance {INSTANCE_NAME!r} did not reach the running state in time"
    )


def deploy_instance(
    config: AwsLightsailConfig,
    frp_token: str,
    *,
    frp_stats_user: str,
    frp_stats_password: str,
    dashboard_admin_password: str,
    ssh_cidr: str | None = None,
) -> str:
    """Create the instance and static IP if missing, then sync firewall rules.

    Idempotent: safe to call again against an already-deployed instance.
    Lightsail has no equivalent of re-running cloud-init on an existing
    instance (user data only applies at launch), so re-running this
    against a live instance only syncs the static IP and firewall rules -
    it never re-provisions frps/Caddy. Returns the instance's public IP.
    """
    client = boto3.client("lightsail", region_name=config.region)

    if describe_instance(config.region) is None:
        user_data = render_user_data(
            domain=config.domain,
            frp_token=frp_token,
            frp_bind_port=DEFAULT_SERVER_PORT,
            frp_vhost_http_port=FRP_VHOST_HTTP_PORT,
            frp_version=FRP_VERSION,
            caddy_version=CADDY_VERSION,
            extra_port_range=config.extra_port_range,
            frp_stats_user=frp_stats_user,
            frp_stats_password=frp_stats_password,
            dashboard_admin_password=dashboard_admin_password,
            no_tls=config.no_tls,
        )
        client.create_instances(
            instanceNames=[INSTANCE_NAME],
            availabilityZone=availability_zone(config.region),
            blueprintId=DEFAULT_BLUEPRINT_ID,
            bundleId=DEFAULT_BUNDLE_ID,
            userData=user_data,
            tags=[{"key": "wildtunnel", "value": "true"}],
        )
        _wait_until_running(client)

    try:
        client.get_static_ip(staticIpName=STATIC_IP_NAME)
    except client.exceptions.NotFoundException:
        client.allocate_static_ip(staticIpName=STATIC_IP_NAME)
    client.attach_static_ip(staticIpName=STATIC_IP_NAME, instanceName=INSTANCE_NAME)

    client.put_instance_public_ports(
        instanceName=INSTANCE_NAME,
        portInfos=port_infos(
            DEFAULT_SERVER_PORT, config.extra_port_range, ssh_cidr=ssh_cidr
        ),
    )

    static_ip: dict[str, Any] = client.get_static_ip(staticIpName=STATIC_IP_NAME)[
        "staticIp"
    ]
    ip_address: str = static_ip["ipAddress"]
    return ip_address


def destroy_instance(region: str) -> None:
    """Delete the instance and release the static IP, tolerating either being absent."""
    client = boto3.client("lightsail", region_name=region)
    try:
        client.delete_instance(instanceName=INSTANCE_NAME, forceDeleteAddOns=True)
    except client.exceptions.NotFoundException:
        pass
    try:
        client.release_static_ip(staticIpName=STATIC_IP_NAME)
    except client.exceptions.NotFoundException:
        pass
