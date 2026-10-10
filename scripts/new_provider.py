#!/usr/bin/env python3
"""Maintainer-only scaffold for a new wildtunnel infra provider.

Not shipped in the installed package and not reachable from the
`wildtunnel` CLI - run directly from the repo root:

    uv run python scripts/new_provider.py <name>

Generates providers/<name>/ and tests/providers/<name>/ skeletons
mirroring providers/aws_ec2's shape (a Click group, a config dataclass
following the nested on-disk schema convention, matching test stubs).
Does not wire the new group into cli.py or pyproject.toml - see the
printed checklist for the remaining manual steps.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_COMMANDS = ("config", "deploy", "destroy", "diff", "status", "info")
_COMMAND_DOCSTRINGS = {
    "config": "Configure the deploy target.",
    "deploy": "Deploy (or update) the stack.",
    "destroy": "Destroy the stack.",
    "diff": "Show pending changes to the stack.",
    "status": "Show the deployed stack's status and outputs.",
    "info": "Show the values needed to configure an frpc client elsewhere.",
}


def _hyphenated(name: str) -> str:
    return name.replace("_", "-")


def _class_prefix(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _cli_py(name: str) -> str:
    hyphenated = _hyphenated(name)
    commands = "\n\n\n".join(
        f"@{name}.command()\n"
        f"def {command}() -> None:\n"
        f'    """{_COMMAND_DOCSTRINGS[command]}"""\n'
        f'    raise click.ClickException("not implemented yet")'
        for command in _COMMANDS
    )
    return (
        "from __future__ import annotations\n"
        "\n"
        "import click\n"
        "\n"
        "# Scaffolded by scripts/new_provider.py. This command vocabulary\n"
        "# mirrors providers/aws_ec2's shape as a starting point - delete\n"
        "# whichever of these don't fit this provider's deploy mechanism\n"
        '# (e.g. a provider with no preview step has no "diff").\n'
        "\n"
        "\n"
        f'@click.group(name="{hyphenated}")\n'
        f"def {name}() -> None:\n"
        f'    """Manage the {hyphenated} infrastructure stack."""\n'
        "\n"
        "\n"
        f"{commands}\n"
    )


def _config_py(name: str) -> str:
    hyphenated = _hyphenated(name)
    class_name = f"{_class_prefix(name)}Config"
    return (
        "from __future__ import annotations\n"
        "\n"
        "import dataclasses\n"
        "from pathlib import Path\n"
        "\n"
        "# Scaffolded by scripts/new_provider.py. On-disk schema convention\n"
        "# (see providers/aws_ec2/config.py for a complete example):\n"
        '# provider-agnostic fields (e.g. "domain") sit top-level in\n'
        f"# config.json; fields specific to this provider nest under a\n"
        f'# "{name}" object, keyed by PROVIDER below. e.g.:\n'
        "#\n"
        "#   domain: str   # provider-agnostic, top-level on disk\n"
        f'#   region: str   # {name}-only, nested under "{name}"\n'
        "\n"
        f'PROVIDER = "{hyphenated}"\n'
        "\n"
        "\n"
        "@dataclasses.dataclass\n"
        f"class {class_name}:\n"
        '    """TODO: replace with this provider\'s real fields."""\n'
        "\n"
        "    @classmethod\n"
        f"    def load(cls, path: Path) -> {class_name}:\n"
        "        raise NotImplementedError(\n"
        '            "see providers/aws_ec2/config.py\'s AwsEc2Config.load for "\n'
        '            "the reference implementation of the nested on-disk schema"\n'
        "        )\n"
        "\n"
        "    def save(self, path: Path) -> None:\n"
        "        raise NotImplementedError(\n"
        '            "see providers/aws_ec2/config.py\'s AwsEc2Config.save for "\n'
        '            "the reference implementation of the nested on-disk schema"\n'
        "        )\n"
    )


def _test_stub(reason: str) -> str:
    return (
        "import pytest\n"
        "\n"
        "\n"
        f'@pytest.mark.skip(reason="{reason}")\n'
        "def test_placeholder() -> None:\n"
        "    pass\n"
    )


def generate_provider(name: str, repo_root: Path) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            f"invalid provider name {name!r}; expected a lowercase, "
            "underscore-separated identifier, e.g. 'aws_lightsail'"
        )

    src_dir = repo_root / "src" / "wildtunnel" / "providers" / name
    tests_dir = repo_root / "tests" / "providers" / name

    if src_dir.exists():
        raise FileExistsError(f"providers/{name}/ already exists at {src_dir}")

    files = {
        src_dir / "__init__.py": "",
        src_dir / "cli.py": _cli_py(name),
        src_dir / "config.py": _config_py(name),
        tests_dir / "__init__.py": "",
        tests_dir / "test_cli.py": _test_stub(
            f"scaffolded by scripts/new_provider.py; replace once "
            f"{name}'s CLI commands are implemented"
        ),
        tests_dir / "test_config.py": _test_stub(
            f"scaffolded by scripts/new_provider.py; replace once "
            f"{name}'s config load/save is implemented"
        ),
    }

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _print_checklist(name: str) -> None:
    print(f"Generated providers/{name}/ and tests/providers/{name}/.")
    print()
    print("Remaining manual steps:")
    print("  1. Wire the group into src/wildtunnel/cli.py:")
    print(f"       from .providers.{name}.cli import {name}")
    print(f"       main.add_command({name})")
    print(
        f"  2. Add an optional extra for {_hyphenated(name)} to "
        "pyproject.toml, if this provider needs one."
    )
    print(
        f"  3. Implement the real deploy logic in providers/{name}/cli.py "
        "and config.py."
    )
    print(f"  4. Fill in tests/providers/{name}/test_cli.py and test_config.py.")
    print("  5. Update the README's provider list.")


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 1:
        print(
            "usage: new_provider.py <name>  "
            "(e.g. uv run python scripts/new_provider.py aws_lightsail)",
            file=sys.stderr,
        )
        return 2

    name = args[0]
    root = (
        repo_root if repo_root is not None else Path(__file__).resolve().parent.parent
    )

    try:
        generate_provider(name, root)
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print_checklist(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
