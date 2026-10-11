from __future__ import annotations

import click
from click.shell_completion import get_completion_class

from .frpc_cli import frpc
from .providers.aws_ec2.cli import aws_ec2
from .providers.aws_lightsail.cli import aws_lightsail

COMPLETE_VAR = "_WILDTUNNEL_COMPLETE"


@click.group()
def main() -> None:
    """Manage your wildtunnel: AWS infra and the frpc client."""


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str) -> None:
    """Print a completion script for SHELL.

    Add one of these to your shell's rc file:

        source <(wildtunnel completion bash)   # ~/.bashrc
        source <(wildtunnel completion zsh)    # ~/.zshrc
        wildtunnel completion fish | source    # ~/.config/fish/config.fish
    """
    comp_cls = get_completion_class(shell)
    assert comp_cls is not None  # guaranteed by click.Choice above
    comp = comp_cls(
        cli=main, ctx_args={}, prog_name="wildtunnel", complete_var=COMPLETE_VAR
    )
    click.echo(comp.source())


main.add_command(aws_ec2)
main.add_command(aws_lightsail)
main.add_command(frpc)
