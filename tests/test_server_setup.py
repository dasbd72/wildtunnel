from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent
    / "src"
    / "wildtunnel"
    / "server"
    / "assets"
    / "server-setup.sh"
).read_text()

_ENV_VAR_CONTRACT = (
    "DOMAIN",
    "FRP_TOKEN",
    "FRP_VERSION",
    "CADDY_VERSION",
    "FRP_BIND_PORT",
    "FRP_VHOST_HTTP_PORT",
    "ALLOW_PORTS",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
)


def test_makes_no_aws_calls() -> None:
    assert "aws ssm" not in _SCRIPT
    assert "aws " not in _SCRIPT
    assert "__REGION__" not in _SCRIPT
    assert "region" not in _SCRIPT.lower()


def test_references_full_env_var_contract() -> None:
    for var in _ENV_VAR_CONTRACT:
        assert f"${{{var}" in _SCRIPT or f"${var}" in _SCRIPT, f"missing ${var}"


def test_has_no_textual_placeholders() -> None:
    # The generic installer takes its inputs as env vars, not
    # synth-time string substitution — no leftover __PLACEHOLDER__ tokens.
    assert "__" not in _SCRIPT


def test_writes_smtp_settings_to_restrictive_dashboard_env_file() -> None:
    # SMTP credentials are secrets like the frp token; the dashboard's env
    # file must not be left world/group readable.
    assert "/etc/wildtunnel/dashboard.env" in _SCRIPT
    assert "chmod 600 /etc/wildtunnel/dashboard.env" in _SCRIPT


def test_detects_cpu_architecture_instead_of_hardcoding_it() -> None:
    # This box may be ARM (EC2 Graviton) or x86_64 (Lightsail has no ARM
    # bundles) — the frp/caddy release asset arch must be detected at
    # boot, never hardcoded to one architecture.
    assert "uname -m" in _SCRIPT
    assert "linux_arm64" not in _SCRIPT
    assert "linux_amd64" not in _SCRIPT
    assert "linux_${ARCH}" in _SCRIPT
