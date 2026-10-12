from __future__ import annotations

import ipaddress
import secrets

import boto3
import click
from botocore.exceptions import ClientError

from ...frpc_config import DEFAULT_SERVER_PORT
from ...paths import (
    AWS_LIGHTSAIL_CONFIG_FILE,
    DASHBOARD_ADMIN_PASSWORD_FILE,
    FRP_STATS_PASSWORD_FILE,
    FRP_TOKEN_FILE,
    other_provider,
)
from ...prompts import confirm_overwrite
from .config import (
    PROVIDER,
    AwsLightsailConfig,
    deploy_instance,
    describe_instance,
    describe_port_states,
    destroy_instance,
    diff_ports,
    port_infos,
)


def _load_config() -> AwsLightsailConfig:
    if not AWS_LIGHTSAIL_CONFIG_FILE.exists():
        raise click.ClickException(
            "not configured; run `wildtunnel aws-lightsail config` first"
        )
    try:
        return AwsLightsailConfig.load(AWS_LIGHTSAIL_CONFIG_FILE)
    except KeyError as exc:
        other = other_provider(AWS_LIGHTSAIL_CONFIG_FILE, PROVIDER)
        if other is not None:
            raise click.ClickException(
                f"config file is currently set up for {other!r}; "
                "run `wildtunnel aws-lightsail config` to switch to this provider"
            ) from exc
        raise click.ClickException(
            "config file is missing or malformed; "
            "run `wildtunnel aws-lightsail config` again"
        ) from exc


def _default_region() -> str | None:
    return boto3.Session().region_name


def _get_or_create_frp_token() -> str:
    # Generated once and reused across deploys so re-running `deploy`
    # doesn't rotate the frp auth token (and force every frpc client to
    # re-`init`) - same reasoning as aws_ec2's app.py. Lightsail instances
    # can't fetch this remotely (no IAM role), so it's baked directly into
    # user data at deploy time instead - see config.py's render_user_data.
    if FRP_TOKEN_FILE.exists():
        return FRP_TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(32)
    FRP_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRP_TOKEN_FILE.write_text(token)
    FRP_TOKEN_FILE.chmod(0o600)
    return token


FRP_STATS_USER = "dashboard"


def _get_or_create_frp_stats_password() -> str:
    # Same reasoning as _get_or_create_frp_token: cached so re-deploys don't
    # rotate the credential frps's webServer and the dashboard app both need
    # to agree on. Purely internal to this VPS (loopback-only), so unlike the
    # frp tunnel token nothing else outside this box ever needs to know it.
    if FRP_STATS_PASSWORD_FILE.exists():
        return FRP_STATS_PASSWORD_FILE.read_text().strip()
    password = secrets.token_urlsafe(32)
    FRP_STATS_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRP_STATS_PASSWORD_FILE.write_text(password)
    FRP_STATS_PASSWORD_FILE.chmod(0o600)
    return password


def _get_or_create_dashboard_admin_password() -> str:
    # Same reasoning as _get_or_create_frp_token: cached so re-deploys
    # don't rotate it. Generated client-side (rather than letting the
    # dashboard pick its own random one on first boot) so `deploy` can
    # print it to the console once provisioning is kicked off.
    if DASHBOARD_ADMIN_PASSWORD_FILE.exists():
        return DASHBOARD_ADMIN_PASSWORD_FILE.read_text().strip()
    password = secrets.token_urlsafe(24)
    DASHBOARD_ADMIN_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_ADMIN_PASSWORD_FILE.write_text(password)
    DASHBOARD_ADMIN_PASSWORD_FILE.chmod(0o600)
    return password


def _parse_ssh_cidr(value: str) -> str:
    # Lightsail's cidrs field (where this value ends up) is IPv4-only -
    # IPv6 ranges need the separate ipv6Cidrs field, which this provider
    # never sets - so validate as IPv4 specifically rather than accepting
    # any CIDR and failing later with an opaque AWS API error.
    try:
        ipaddress.IPv4Network(value, strict=False)
    except ValueError as exc:
        raise click.BadParameter(
            f"invalid --ssh-cidr {value!r}; expected an IPv4 CIDR like 1.2.3.4/32",
            param_hint="--ssh-cidr",
        ) from exc
    return value


_SSH_CIDR_HELP = (
    "Additionally open SSH (port 22) to this CIDR, e.g. 1.2.3.4/32 for your "
    "current IP, for a real SSH client. By default SSH is only reachable "
    "via the Lightsail console's browser-based SSH (the 'lightsail-connect' "
    "firewall alias) - not opened to any public IP. One-shot: not saved to "
    "config, so omit it on a later deploy/diff to close client SSH again."
)


def _parse_port_range(value: str) -> tuple[int, int]:
    start_str, sep, end_str = value.partition("-")
    if not sep or not start_str.isdigit() or not end_str.isdigit():
        raise click.BadParameter(
            f"invalid --extra-port-range {value!r}; expected START-END",
            param_hint="--extra-port-range",
        )
    start, end = int(start_str), int(end_str)
    if not (1 <= start <= end <= 65535):
        raise click.BadParameter(
            f"invalid --extra-port-range {value!r}; expected 1 <= START <= END <= 65535",
            param_hint="--extra-port-range",
        )
    return start, end


@click.group(name="aws-lightsail")
def aws_lightsail() -> None:
    """Manage the AWS Lightsail infrastructure (instance + frps + Caddy) stack."""


@aws_lightsail.command()
@click.option(
    "--domain",
    required=True,
    help="Base domain for the tunnel, e.g. example.com. Point a wildcard "
    "record (*.DOMAIN) at the deployed static IP in your DNS provider.",
)
@click.option(
    "--region",
    default=None,
    help="AWS region to deploy into. Defaults to your AWS CLI/config default region.",
)
@click.option(
    "--extra-port-range",
    default=None,
    metavar="START-END",
    help="Open this TCP+UDP port range on the Lightsail instance and in "
    "frps, for non-HTTP tunnels added via `frpc add --proto tcp|udp "
    "--remote-port`. Not opened at all unless given.",
)
@click.option(
    "--no-tls",
    is_flag=True,
    help="Provision Caddy in plain-HTTP (no-TLS) mode: no Let's Encrypt, no "
    "HTTP->HTTPS redirect. Defaults to TLS. Only sets the initial mode for a "
    "fresh deploy; toggle a live box with wildtunnel-tls-enable/disable.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Overwrite an existing configuration without confirming.",
)
def config(
    domain: str,
    region: str | None,
    extra_port_range: str | None,
    no_tls: bool,
    yes: bool,
) -> None:
    """Configure the domain/region that deploy/destroy/diff/status target."""
    region = region or _default_region()
    if not region:
        raise click.ClickException(
            "no region given and none configured; pass --region or set a default "
            "with `aws configure set region <region>`"
        )
    port_range = _parse_port_range(extra_port_range) if extra_port_range else None
    new_config = AwsLightsailConfig(
        domain=domain,
        region=region,
        extra_port_range=port_range,
        no_tls=no_tls,
    )

    # A config file only holds another provider's shape if it was
    # manually copied/edited (see other_provider() in paths.py) - in
    # that case there's nothing of this provider's shape to compare
    # against, so switch over silently instead of crashing on load.
    if (
        AWS_LIGHTSAIL_CONFIG_FILE.exists()
        and other_provider(AWS_LIGHTSAIL_CONFIG_FILE, PROVIDER) is None
    ):
        confirm_overwrite(
            AwsLightsailConfig.load(AWS_LIGHTSAIL_CONFIG_FILE), new_config, yes=yes
        )

    new_config.save(AWS_LIGHTSAIL_CONFIG_FILE)
    click.echo(f"Configured: domain={domain} region={region}")


@aws_lightsail.command()
@click.option(
    "--ssh-cidr",
    default=None,
    metavar="CIDR",
    help=_SSH_CIDR_HELP,
)
def deploy(ssh_cidr: str | None) -> None:
    """Deploy (or update) the Lightsail instance.

    Only provisions frps/Caddy on first create - Lightsail has no
    equivalent of re-running cloud-init on an existing instance, so
    re-running this against a live instance only syncs the static IP and
    firewall rules (e.g. after changing --extra-port-range or --ssh-cidr).
    """
    if ssh_cidr is not None:
        ssh_cidr = _parse_ssh_cidr(ssh_cidr)
    aws_lightsail_config = _load_config()
    token = _get_or_create_frp_token()
    admin_password = _get_or_create_dashboard_admin_password()
    try:
        public_ip = deploy_instance(
            aws_lightsail_config,
            token,
            frp_stats_user=FRP_STATS_USER,
            frp_stats_password=_get_or_create_frp_stats_password(),
            dashboard_admin_password=admin_password,
            ssh_cidr=ssh_cidr,
        )
    except (ClientError, TimeoutError) as exc:
        raise click.ClickException(f"deploy failed: {exc}") from exc
    click.echo(f"deployed; static IP: {public_ip}")
    click.echo(f"dashboard admin password: {admin_password}")


@aws_lightsail.command()
def destroy() -> None:
    """Destroy the Lightsail instance and release its static IP."""
    aws_lightsail_config = _load_config()
    try:
        destroy_instance(aws_lightsail_config.region)
    except ClientError as exc:
        raise click.ClickException(f"destroy failed: {exc}") from exc
    click.echo("destroyed")


@aws_lightsail.command()
@click.option(
    "--ssh-cidr",
    default=None,
    metavar="CIDR",
    help=_SSH_CIDR_HELP + " Pass the same value used on `deploy` to avoid false drift.",
)
def diff(ssh_cidr: str | None) -> None:
    """Show firewall rule drift between the desired and live configuration.

    There's no CloudFormation changeset here (deploy is direct boto3
    calls, not CDK), so this only diffs firewall rules against what
    `deploy` would set - it can't preview instance creation/destruction.
    """
    if ssh_cidr is not None:
        ssh_cidr = _parse_ssh_cidr(ssh_cidr)
    aws_lightsail_config = _load_config()
    current = describe_port_states(aws_lightsail_config.region)
    if current is None:
        click.echo("no instance deployed; run `wildtunnel aws-lightsail deploy` first")
        return

    desired = port_infos(
        DEFAULT_SERVER_PORT, aws_lightsail_config.extra_port_range, ssh_cidr=ssh_cidr
    )
    to_open, to_close = diff_ports(desired, current)
    if not to_open and not to_close:
        click.echo("firewall rules are in sync (this only checks firewall rules)")
        return
    for entry in to_open:
        click.echo(f"+ open {entry['protocol']} {entry['fromPort']}-{entry['toPort']}")
    for entry in to_close:
        click.echo(f"- close {entry['protocol']} {entry['fromPort']}-{entry['toPort']}")


@aws_lightsail.command()
def status() -> None:
    """Show the deployed instance's status and public IP."""
    aws_lightsail_config = _load_config()
    instance = describe_instance(aws_lightsail_config.region)
    if instance is None:
        click.echo("not deployed; run `wildtunnel aws-lightsail deploy` first")
        return
    click.echo(f"State: {instance['state']['name']}")
    click.echo(f"PublicIp: {instance.get('publicIpAddress', '')}")
    click.echo(f"IsStaticIp: {instance.get('isStaticIp', False)}")


@aws_lightsail.command()
def info() -> None:
    """Show the values needed to configure an frpc client elsewhere.

    frpc is decoupled from the infra side: it takes server-addr,
    base-domain and token as explicit flags. This command reads those
    off the deployed instance and the locally cached frp token so you
    can copy them to the frpc machine. Unlike aws-ec2 (which stores the
    token in SSM), the token here only exists on the machine that ran
    `deploy` - there's no remote copy to fetch it from.
    """
    aws_lightsail_config = _load_config()
    instance = describe_instance(aws_lightsail_config.region)
    if instance is None:
        raise click.ClickException(
            "not deployed; run `wildtunnel aws-lightsail deploy` first"
        )
    server_addr = instance.get("publicIpAddress")
    if not server_addr:
        raise click.ClickException(
            "instance has no public IP yet; wait for it to finish starting"
        )
    if not FRP_TOKEN_FILE.exists():
        raise click.ClickException(
            "no local frp token found; run `info` on the machine that ran "
            f"`deploy`, or copy {FRP_TOKEN_FILE} from it"
        )
    token = FRP_TOKEN_FILE.read_text().strip()

    click.echo(f"server-addr:  {server_addr}")
    click.echo(f"base-domain:  {aws_lightsail_config.domain}")
    click.echo(f"server-port:  {DEFAULT_SERVER_PORT}")
    click.echo(f"token:        {token}")
    click.echo("")
    click.echo("Run this on the frpc machine:")
    click.echo(
        f"  wildtunnel frpc config \\\n"
        f"    --server-addr {server_addr} \\\n"
        f"    --base-domain {aws_lightsail_config.domain} \\\n"
        f"    --token {token}"
    )
