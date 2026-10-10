from __future__ import annotations

from typing import Any

import click


def confirm_overwrite(old: Any, new: Any, *, yes: bool) -> None:
    """Prompt before replacing `old` with `new`, unless --yes or nothing changed."""
    if yes or old == new:
        return
    click.echo(f"Currently configured: {old}")
    click.echo(f"New configuration:    {new}")
    click.confirm("Overwrite?", abort=True)
