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
    "FRP_STATS_USER",
    "FRP_STATS_PASSWORD",
    "DASHBOARD_PORT",
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


def test_dashboard_service_receives_smtp_settings() -> None:
    # The dashboard's forgot-password email flow reads its generic SMTP
    # relay config from the same systemd unit as its other settings, the
    # same way FRP_STATS_USER/PASSWORD are threaded today.
    assert "Environment=SMTP_HOST=${SMTP_HOST}" in _SCRIPT
    assert "Environment=SMTP_PORT=${SMTP_PORT}" in _SCRIPT
    assert "Environment=SMTP_USERNAME=${SMTP_USERNAME}" in _SCRIPT
    assert "Environment=SMTP_PASSWORD=${SMTP_PASSWORD}" in _SCRIPT


def test_detects_cpu_architecture_instead_of_hardcoding_it() -> None:
    # This box may be ARM (EC2 Graviton) or x86_64 (Lightsail has no ARM
    # bundles) — the frp/caddy release asset arch must be detected at
    # boot, never hardcoded to one architecture.
    assert "uname -m" in _SCRIPT
    assert "linux_arm64" not in _SCRIPT
    assert "linux_amd64" not in _SCRIPT
    assert "linux_${ARCH}" in _SCRIPT


def test_webserver_stats_are_bound_to_loopback_only() -> None:
    # The frps stats API that StatsSource polls must never be reachable
    # from outside the box.
    assert 'webServer.addr = "127.0.0.1"' in _SCRIPT
    assert "webServer.port" in _SCRIPT


def test_webserver_stats_require_basic_auth_credentials() -> None:
    assert "webServer.user" in _SCRIPT
    assert "webServer.password" in _SCRIPT
    assert "${FRP_STATS_USER}" in _SCRIPT
    assert "${FRP_STATS_PASSWORD}" in _SCRIPT


def test_installs_and_enables_dashboard_systemd_service() -> None:
    assert "wildtunnel-dashboard.service" in _SCRIPT
    assert "wildtunnel[dashboard]" in _SCRIPT
    assert "systemctl enable --now wildtunnel-dashboard" in _SCRIPT


def test_dashboard_binds_loopback_only() -> None:
    # No new security-group/firewall port is opened for the dashboard -
    # it must only ever be reachable via Caddy on 127.0.0.1.
    assert "127.0.0.1:${DASHBOARD_PORT}" in _SCRIPT
    assert "0.0.0.0:${DASHBOARD_PORT}" not in _SCRIPT


def test_dashboard_bootstraps_admin_before_serving() -> None:
    # The plaintext bootstrap password secret file must exist before the
    # app starts accepting requests (see dashboard.bootstrap.ensure_bootstrapped).
    assert "wildtunnel.dashboard.main" in _SCRIPT


def test_caddyfile_routes_wildtunnel_subdomain_to_dashboard_ahead_of_wildcard() -> None:
    caddyfile_start = _SCRIPT.index("cat > /etc/caddy/Caddyfile")
    wildcard_start = _SCRIPT.index("*.${DOMAIN} {", caddyfile_start)
    dashboard_start = _SCRIPT.index("wildtunnel.${DOMAIN} {", caddyfile_start)

    assert dashboard_start < wildcard_start, (
        "the host-matched wildtunnel.${DOMAIN} block must come before the "
        "*.${DOMAIN} wildcard block so Caddy prefers the specific match"
    )
    dashboard_block = _SCRIPT[dashboard_start:wildcard_start]
    assert "on_demand" in dashboard_block
    assert "reverse_proxy 127.0.0.1:${DASHBOARD_PORT}" in dashboard_block
