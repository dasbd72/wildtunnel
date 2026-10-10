from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Any

import click

from .frpc_config import DEFAULT_SERVER_PORT, FrpcConfig
from .frpc_toml import parse_target
from .frpc_toml import render as render_frpc_toml
from .paths import (
    FRPC_CONFIG_FILE,
    FRPC_TOML_FILE,
    SERVICES_FILE,
    SYSTEMD_UNIT_FILE,
)
from .prompts import confirm_overwrite
from .services import Service, load_services, save_services
from .systemd_unit import render as render_systemd_unit


def _require_frpc() -> str:
    frpc_path = shutil.which("frpc")
    if frpc_path is None:
        raise click.ClickException(
            "frpc not found on PATH. Install it yourself from "
            "https://github.com/fatedier/frp/releases."
        )
    return frpc_path


def _parse_services(pairs: tuple[str, ...]) -> dict[str, Service]:
    services: dict[str, Service] = {}
    for pair in pairs:
        name, sep, rest = pair.partition("=")
        if not sep or not name:
            raise click.BadParameter(
                f"invalid --service {pair!r}; expected NAME=HOST:PORT"
                "[/PROTO:REMOTE_PORT]",
                param_hint="--service",
            )
        target, proto_sep, proto_spec = rest.partition("/")
        try:
            parse_target(target)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--service") from exc

        proto = "http"
        remote_port: int | None = None
        if proto_sep:
            proto, _, remote_port_str = proto_spec.partition(":")
            if not remote_port_str.isdigit():
                raise click.BadParameter(
                    f"invalid --service {pair!r}; expected NAME=HOST:PORT"
                    "[/PROTO:REMOTE_PORT]",
                    param_hint="--service",
                )
            remote_port = int(remote_port_str)

        try:
            services[name] = Service(
                target=target, proto=proto, remote_port=remote_port
            )
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--service") from exc
    return services


def _build_config(
    server_addr: str,
    base_domain: str,
    server_port: int,
    token: str,
    services: tuple[str, ...],
) -> tuple[FrpcConfig, dict[str, Service]]:
    # Decoupled from the infra side: every value comes straight from the
    # caller's flags. Run `wildtunnel aws-ec2 info` on the deploying
    # machine to read them off the deployed stack.
    config = FrpcConfig(
        server_addr=server_addr,
        base_domain=base_domain,
        token=token,
        server_port=server_port,
    )
    return config, _parse_services(services)


def _write_frpc_toml(config: FrpcConfig, services: dict[str, Service]) -> None:
    FRPC_TOML_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRPC_TOML_FILE.write_text(render_frpc_toml(config, services))
    FRPC_TOML_FILE.chmod(0o600)


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["systemctl", "--user", *args], check=check)


def _load_config() -> FrpcConfig:
    if not FRPC_CONFIG_FILE.exists():
        raise click.ClickException("not configured; run `wildtunnel frpc config` first")
    return FrpcConfig.load(FRPC_CONFIG_FILE)


def _apply_services(cfg: FrpcConfig, services: dict[str, Service]) -> None:
    save_services(services, SERVICES_FILE)
    _write_frpc_toml(cfg, services)
    _systemctl("restart", "frpc.service")


def _common_options(f: Callable[..., Any]) -> Callable[..., Any]:
    f = click.option(
        "--server-addr",
        required=True,
        help="Public address of the frps server (EC2 Elastic IP or hostname). "
        "Read it off the deployed stack with `wildtunnel aws-ec2 info`.",
    )(f)
    f = click.option(
        "--base-domain",
        required=True,
        help="Base domain the wildcard DNS record covers, e.g. dasbd72.com. "
        "Read it off the deployed stack with `wildtunnel aws-ec2 info`.",
    )(f)
    f = click.option(
        "--server-port", default=DEFAULT_SERVER_PORT, show_default=True, type=int
    )(f)
    f = click.option(
        "--token",
        required=True,
        help="The frp auth token. Read it off the deployed stack with "
        "`wildtunnel aws-ec2 info`.",
    )(f)
    f = click.option(
        "--service",
        "services",
        multiple=True,
        metavar="NAME=HOST:PORT[/PROTO:REMOTE_PORT]",
        help="Expose a local HOST:PORT at NAME.<base-domain> (http, default), or "
        "as an explicit PROTO (tcp/udp) proxy bound to REMOTE_PORT on the frps "
        "server. Repeatable.",
    )(f)
    return f


@click.group()
def frpc() -> None:
    """Manage the frpc side of your wildtunnel."""


@frpc.command()
@_common_options
def render(
    server_addr: str,
    base_domain: str,
    server_port: int,
    token: str,
    services: tuple[str, ...],
) -> None:
    """Render an frpc.toml to stdout from flags only (no persistence)."""
    config, services_dict = _build_config(
        server_addr, base_domain, server_port, token, services
    )
    click.echo(render_frpc_toml(config, services_dict), nl=False)


@frpc.command()
@_common_options
@click.option(
    "--yes",
    is_flag=True,
    help="Overwrite an existing configuration without confirming.",
)
def config(
    server_addr: str,
    base_domain: str,
    server_port: int,
    token: str,
    services: tuple[str, ...],
    yes: bool,
) -> None:
    """Configure, persist, and start the local frpc tunnel client.

    An extension of `render`: builds the same settings, then saves them
    and (re)starts the systemd service. If --service is not given, the
    previously registered services (managed via `add`/`del`) are kept as-is.
    """
    frpc_path = _require_frpc()

    new_config, service_overrides = _build_config(
        server_addr, base_domain, server_port, token, services
    )

    if FRPC_CONFIG_FILE.exists():
        confirm_overwrite(FrpcConfig.load(FRPC_CONFIG_FILE), new_config, yes=yes)

    resolved_services = service_overrides if services else load_services(SERVICES_FILE)
    if services:
        save_services(resolved_services, SERVICES_FILE)

    new_config.save(FRPC_CONFIG_FILE)
    _write_frpc_toml(new_config, resolved_services)

    SYSTEMD_UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT_FILE.write_text(render_systemd_unit(frpc_path, str(FRPC_TOML_FILE)))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", "frpc.service")
    _systemctl("restart", "frpc.service")
    click.echo(f"frpc configured and started ({SYSTEMD_UNIT_FILE})")


@frpc.command()
@click.argument("name")
@click.argument("target")
@click.option(
    "--proto",
    type=click.Choice(["http", "tcp", "udp"]),
    default="http",
    show_default=True,
    help="Proxy type. tcp/udp require --remote-port instead of a subdomain.",
)
@click.option(
    "--remote-port",
    type=int,
    default=None,
    help="Public port to bind on the frps server. Required for --proto tcp/udp.",
)
def add(name: str, target: str, proto: str, remote_port: int | None) -> None:
    """Expose a local TARGET (host:port) at NAME.<base-domain>, or on --remote-port."""
    try:
        parse_target(target)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="TARGET") from exc
    try:
        service = Service(target=target, proto=proto, remote_port=remote_port)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--remote-port") from exc
    cfg = _load_config()
    services = load_services(SERVICES_FILE)
    services[name] = service
    _apply_services(cfg, services)
    if proto == "http":
        click.echo(f"{name}.{cfg.base_domain} -> {target}")
    else:
        click.echo(f"{cfg.server_addr}:{remote_port} ({proto}) -> {target}")


@frpc.command(name="del")
@click.argument("name")
def del_(name: str) -> None:
    """Stop exposing NAME."""
    cfg = _load_config()
    services = load_services(SERVICES_FILE)
    if name not in services:
        raise click.ClickException(f"no such service: {name}")
    del services[name]
    _apply_services(cfg, services)
    click.echo(f"removed {name}")


@frpc.command(name="list")
def list_services() -> None:
    """List configured services."""
    cfg = _load_config()
    services = load_services(SERVICES_FILE)
    if not services:
        click.echo("no services configured")
        return
    for name, service in sorted(services.items()):
        if service.proto == "http":
            click.echo(f"{name}.{cfg.base_domain} -> {service.target}")
        else:
            click.echo(
                f"{cfg.server_addr}:{service.remote_port} "
                f"({service.proto}) -> {service.target}"
            )


@frpc.command()
def status() -> None:
    """Show frpc service status."""
    # `systemctl status` exits non-zero whenever the unit isn't active
    # (e.g. restarting, stopped, failed) - that's its normal reporting
    # mechanism, not a failure of this command, so don't raise on it.
    result = _systemctl("status", "frpc.service", check=False)
    raise SystemExit(result.returncode)
