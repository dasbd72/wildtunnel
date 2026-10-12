from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from typing import Any

import boto3
import click
from botocore.exceptions import ClientError

from ... import token as token_module
from ...frpc_config import DEFAULT_SERVER_PORT
from ...paths import (
    AWS_EC2_CACHE_DIR,
    AWS_EC2_CONFIG_FILE,
    DASHBOARD_ADMIN_PASSWORD_FILE,
    other_provider,
)
from ...prompts import confirm_overwrite
from .config import FRP_TOKEN_PARAM, PROVIDER, STACK_NAME, AwsEc2Config, describe_stack


def _require_cdk() -> str:
    cdk = shutil.which("cdk")
    if cdk is None:
        raise click.ClickException(
            "cdk not found on PATH. Install the aws-ec2 extra — e.g. "
            "`uv tool install 'wildtunnel[aws-ec2]'` — then re-run this command."
        )
    return cdk


def _require_aws_cdk_lib() -> None:
    try:
        importlib.import_module("aws_cdk")
    except ImportError as exc:
        raise click.ClickException(
            "aws-cdk-lib is not installed. Install the aws-ec2 extra — e.g. "
            "`uv tool install 'wildtunnel[aws-ec2]'` — then re-run this command."
        ) from exc


def _load_config() -> AwsEc2Config:
    if not AWS_EC2_CONFIG_FILE.exists():
        raise click.ClickException(
            "not configured; run `wildtunnel aws-ec2 config` first"
        )
    try:
        return AwsEc2Config.load(AWS_EC2_CONFIG_FILE)
    except KeyError as exc:
        other = other_provider(AWS_EC2_CONFIG_FILE, PROVIDER)
        if other is not None:
            raise click.ClickException(
                f"config file is currently set up for {other!r}; "
                "run `wildtunnel aws-ec2 config` to switch to this provider"
            ) from exc
        raise click.ClickException(
            "config file is missing or malformed; run `wildtunnel aws-ec2 config` again"
        ) from exc


def _default_region() -> str | None:
    return boto3.Session().region_name


def _run_cdk(subcommand: str, aws_ec2_config: AwsEc2Config) -> None:
    cdk = _require_cdk()
    _require_aws_cdk_lib()
    AWS_EC2_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    context_args = [
        "-c",
        f"domain={aws_ec2_config.domain}",
        "-c",
        f"region={aws_ec2_config.region}",
    ]
    if aws_ec2_config.extra_port_range is not None:
        start, end = aws_ec2_config.extra_port_range
        context_args += [
            "-c",
            f"extraPortRangeStart={start}",
            "-c",
            f"extraPortRangeEnd={end}",
        ]
    if aws_ec2_config.no_tls:
        context_args += ["-c", "noTls=true"]
    try:
        subprocess.run(
            [
                cdk,
                "-a",
                f"{sys.executable} -m wildtunnel.providers.aws_ec2.app",
                *context_args,
                subcommand,
            ],
            cwd=AWS_EC2_CACHE_DIR,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"cdk {subcommand} failed (exit code {exc.returncode})"
        ) from exc


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


@click.group(name="aws-ec2")
def aws_ec2() -> None:
    """Manage the AWS EC2 infrastructure (EC2 + frps + Caddy) stack."""


@aws_ec2.command()
@click.option(
    "--domain",
    required=True,
    help="Base domain for the tunnel, e.g. example.com. Point a wildcard "
    "record (*.DOMAIN) at the deployed Elastic IP in your DNS provider.",
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
    help="Open this TCP+UDP port range on the EC2 host and in frps, for "
    "non-HTTP tunnels added via `frpc add --proto tcp|udp --remote-port`. "
    "Not opened at all unless given.",
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
    new_config = AwsEc2Config(
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
        AWS_EC2_CONFIG_FILE.exists()
        and other_provider(AWS_EC2_CONFIG_FILE, PROVIDER) is None
    ):
        confirm_overwrite(AwsEc2Config.load(AWS_EC2_CONFIG_FILE), new_config, yes=yes)

    new_config.save(AWS_EC2_CONFIG_FILE)
    click.echo(f"Configured: domain={domain} region={region}")


@aws_ec2.command()
def deploy() -> None:
    """Deploy (or update) the AWS stack."""
    _run_cdk("deploy", _load_config())
    if DASHBOARD_ADMIN_PASSWORD_FILE.exists():
        password = DASHBOARD_ADMIN_PASSWORD_FILE.read_text().strip()
        click.echo(f"dashboard admin password: {password}")


@aws_ec2.command()
def destroy() -> None:
    """Destroy the AWS stack."""
    _run_cdk("destroy", _load_config())


@aws_ec2.command()
def diff() -> None:
    """Show pending changes to the AWS stack."""
    _run_cdk("diff", _load_config())


def _describe_deployed_stack(region: str) -> dict[str, Any]:
    try:
        return describe_stack(region)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationError":
            raise click.ClickException(
                f"stack {STACK_NAME!r} not found; run `wildtunnel aws-ec2 deploy` first"
            ) from exc
        raise click.ClickException(
            f"could not look up the deployed stack: {exc}"
        ) from exc


def _stack_output(stack: dict[str, Any], key: str) -> str | None:
    for output in stack.get("Outputs", []):
        if output["OutputKey"] == key:
            value: str = output["OutputValue"]
            return value
    return None


@aws_ec2.command()
def status() -> None:
    """Show the deployed stack's status and outputs."""
    aws_ec2_config = _load_config()
    stack = _describe_deployed_stack(aws_ec2_config.region)
    click.echo(f"Status: {stack['StackStatus']}")
    for output in stack.get("Outputs", []):
        click.echo(f"{output['OutputKey']}: {output['OutputValue']}")


@aws_ec2.command()
def info() -> None:
    """Show the values needed to configure an frpc client elsewhere.

    frpc is decoupled from the infra side: it takes server-addr, base-domain
    and token as explicit flags. This command reads those off the deployed
    stack (Elastic IP + SSM token) so you can copy them to the frpc machine.
    """
    aws_ec2_config = _load_config()
    stack = _describe_deployed_stack(aws_ec2_config.region)

    server_addr = _stack_output(stack, "ElasticIp")
    if server_addr is None:
        raise click.ClickException(
            f"stack {STACK_NAME!r} has no ElasticIp output; "
            "re-run `wildtunnel aws-ec2 deploy`"
        )
    try:
        token = token_module.fetch_token(FRP_TOKEN_PARAM, aws_ec2_config.region)
    except ClientError as exc:
        raise click.ClickException(
            f"could not fetch the frp token from SSM parameter {FRP_TOKEN_PARAM!r}: {exc}"
        ) from exc

    click.echo(f"server-addr:  {server_addr}")
    click.echo(f"base-domain:  {aws_ec2_config.domain}")
    click.echo(f"server-port:  {DEFAULT_SERVER_PORT}")
    click.echo(f"token:        {token}")
    click.echo("")
    click.echo("Run this on the frpc machine:")
    click.echo(
        f"  wildtunnel frpc config \\\n"
        f"    --server-addr {server_addr} \\\n"
        f"    --base-domain {aws_ec2_config.domain} \\\n"
        f"    --token {token}"
    )
