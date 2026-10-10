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
