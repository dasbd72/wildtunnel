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
    "NO_TLS",
    "FRP_STATS_USER",
    "FRP_STATS_PASSWORD",
    "DASHBOARD_PORT",
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


def test_dashboard_service_receives_initial_admin_password() -> None:
    # Generated client-side by the deploying CLI (mirroring frp_token) so
    # it can be printed to the console when `deploy` finishes. Empty is a
    # valid, supported value - no `:?` required-check, since the dashboard
    # falls back to generating its own random password when unset.
    assert (
        "Environment=DASHBOARD_INITIAL_ADMIN_PASSWORD=${DASHBOARD_INITIAL_ADMIN_PASSWORD}"
        in _SCRIPT
    )


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


def test_dashboard_venv_uses_a_python_new_enough_for_wildtunnel() -> None:
    # pyproject.toml requires Python >=3.12, but Amazon Linux 2023's
    # default `python3` is 3.9 - a venv built with the bare `python3`
    # command silently gets a too-old interpreter and `pip install
    # wildtunnel[dashboard]` fails to find any compatible release.
    assert "dnf install -y python3.12" in _SCRIPT
    assert "python3.12 -m venv" in _SCRIPT
    assert "python3 -m venv" not in _SCRIPT


def test_dashboard_binds_loopback_only() -> None:
    # No new security-group/firewall port is opened for the dashboard -
    # it must only ever be reachable via Caddy on 127.0.0.1.
    assert "127.0.0.1:${DASHBOARD_PORT}" in _SCRIPT
    assert "0.0.0.0:${DASHBOARD_PORT}" not in _SCRIPT


def test_dashboard_bootstraps_admin_before_serving() -> None:
    # The plaintext bootstrap password secret file must exist before the
    # app starts accepting requests (see dashboard.bootstrap.ensure_bootstrapped).
    assert "wildtunnel.dashboard.main" in _SCRIPT


def test_installs_reset_password_executable() -> None:
    assert "/usr/local/bin/wildtunnel-reset-password" in _SCRIPT


def test_reset_password_wrapper_targets_the_live_dashboard_database() -> None:
    # Must match the same literal paths the dashboard systemd unit uses,
    # so a reset operates on the exact database/secret file in use.
    assert "DASHBOARD_DB_PATH=/var/lib/wildtunnel-dashboard/dashboard.db" in _SCRIPT
    assert (
        "DASHBOARD_SECRET_FILE=/var/lib/wildtunnel-dashboard/admin-password.txt"
        in _SCRIPT
    )


def test_reset_password_wrapper_invokes_reset_password_module() -> None:
    assert (
        "/opt/wildtunnel-dashboard/bin/python -m wildtunnel.dashboard.reset_password"
        in _SCRIPT
    )


def test_reset_password_wrapper_is_made_executable() -> None:
    assert "chmod +x /usr/local/bin/wildtunnel-reset-password" in _SCRIPT


def test_installs_upgrade_executable() -> None:
    assert "/usr/local/bin/wildtunnel-upgrade" in _SCRIPT


def test_upgrade_wrapper_upgrades_the_dashboard_venv_package() -> None:
    assert (
        '/opt/wildtunnel-dashboard/bin/pip install --upgrade "wildtunnel[dashboard]"'
        in (_SCRIPT)
    )


def test_upgrade_wrapper_restarts_the_dashboard_service() -> None:
    upgrade_start = _SCRIPT.index("cat > /usr/local/bin/wildtunnel-upgrade")
    upgrade_end = _SCRIPT.index("chmod +x /usr/local/bin/wildtunnel-upgrade")
    upgrade_wrapper = _SCRIPT[upgrade_start:upgrade_end]

    assert "systemctl restart wildtunnel-dashboard" in upgrade_wrapper


def test_upgrade_wrapper_is_made_executable() -> None:
    assert "chmod +x /usr/local/bin/wildtunnel-upgrade" in _SCRIPT


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


def test_writes_both_caddyfile_variants() -> None:
    # The toggle executables need both variants on disk, regardless of the
    # initial mode, so the box can be flipped in either direction later.
    assert "cat > /etc/caddy/Caddyfile.tls" in _SCRIPT
    assert "cat > /etc/caddy/Caddyfile.plain" in _SCRIPT


def test_plain_caddyfile_serves_http_without_tls_or_redirect() -> None:
    plain_start = _SCRIPT.index("cat > /etc/caddy/Caddyfile.plain")
    seed_start = _SCRIPT.index("# --- Active config + state file")
    plain = _SCRIPT[plain_start:seed_start]

    # Explicit http:// scheme defeats Caddy auto-HTTPS; no on-demand TLS
    # permission check and no HTTP->HTTPS redirect in plain mode.
    assert "http://wildtunnel.${DOMAIN}" in plain
    assert "http://*.${DOMAIN}" in plain
    assert "reverse_proxy 127.0.0.1:${DASHBOARD_PORT}" in plain
    assert "reverse_proxy 127.0.0.1:${FRP_VHOST_HTTP_PORT}" in plain
    assert "on_demand" not in plain
    assert "redir https" not in plain


def test_seeds_active_caddyfile_and_state_file_from_no_tls() -> None:
    # The active Caddyfile and the state file are both derived from NO_TLS.
    assert 'if [[ -n "${NO_TLS}" ]]; then' in _SCRIPT
    assert "TLS_MODE=plain" in _SCRIPT
    assert "TLS_MODE=tls" in _SCRIPT
    assert 'cp "/etc/caddy/Caddyfile.${TLS_MODE}" /etc/caddy/Caddyfile' in _SCRIPT
    assert 'echo "${TLS_MODE}" > /etc/caddy/tls-mode' in _SCRIPT


def test_installs_tls_toggle_executables() -> None:
    assert "/usr/local/bin/wildtunnel-tls-enable" in _SCRIPT
    assert "/usr/local/bin/wildtunnel-tls-disable" in _SCRIPT


def test_tls_toggle_executables_are_made_executable() -> None:
    assert "chmod +x /usr/local/bin/wildtunnel-tls-enable" in _SCRIPT
    assert "chmod +x /usr/local/bin/wildtunnel-tls-disable" in _SCRIPT


def test_tls_enable_swaps_to_tls_variant_and_reloads_caddy() -> None:
    enable_start = _SCRIPT.index("cat > /usr/local/bin/wildtunnel-tls-enable")
    enable_end = _SCRIPT.index("chmod +x /usr/local/bin/wildtunnel-tls-enable")
    enable = _SCRIPT[enable_start:enable_end]

    assert "cp /etc/caddy/Caddyfile.tls /etc/caddy/Caddyfile" in enable
    assert 'echo tls > "${STATE_FILE}"' in enable
    assert "systemctl reload caddy" in enable


def test_tls_disable_swaps_to_plain_variant_and_reloads_caddy() -> None:
    disable_start = _SCRIPT.index("cat > /usr/local/bin/wildtunnel-tls-disable")
    disable_end = _SCRIPT.index("chmod +x /usr/local/bin/wildtunnel-tls-disable")
    disable = _SCRIPT[disable_start:disable_end]

    assert "cp /etc/caddy/Caddyfile.plain /etc/caddy/Caddyfile" in disable
    assert 'echo plain > "${STATE_FILE}"' in disable
    assert "systemctl reload caddy" in disable


def test_tls_toggle_executables_are_idempotent() -> None:
    # Re-running a toggle that is already in effect is a safe no-op.
    assert 'echo "TLS is already enabled"' in _SCRIPT
    assert 'echo "TLS is already disabled"' in _SCRIPT
