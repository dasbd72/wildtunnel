from __future__ import annotations

import click

from .frpc_cli import frpc
from .providers.aws_ec2.cli import aws_ec2
from .providers.aws_lightsail.cli import aws_lightsail


@click.group()
def main() -> None:
    """Manage your wildtunnel: AWS infra and the frpc client."""


main.add_command(aws_ec2)
main.add_command(aws_lightsail)
main.add_command(frpc)
