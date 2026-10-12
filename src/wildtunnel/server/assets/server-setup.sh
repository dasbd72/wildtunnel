#!/bin/bash
set -euo pipefail

# Wildtunnel server installer. Two modes:
#   provision (default) - first boot: install frps/Caddy, create the dashboard
#                         venv, write the secret-bearing config, then configure.
#   configure           - idempotent re-apply of the non-secret config (admin
#                         executables + Caddyfile variants). `wildtunnel-upgrade`
#                         invokes it to deliver shell-level changes to an
#                         already-deployed box. Reads inputs from the state file
#                         written at provision time; needs no env vars.
MODE="${1:-provision}"

# Non-secret inputs the configure phase needs to regenerate the Caddyfile
# variants, persisted at provision time so `configure` can run on a live box
# with no deploy-time environment. Secret-bearing inputs (frp token, stats
# creds, bootstrap password) are NOT stored here - they live only in their
# 0600 files and are never rewritten by the configure phase.
STATE_FILE=/etc/wildtunnel/config.env

if [[ "${MODE}" == "provision" ]]; then
  : "${DOMAIN:?DOMAIN is required}"
  : "${FRP_TOKEN:?FRP_TOKEN is required}"
  : "${FRP_VERSION:?FRP_VERSION is required}"
  : "${CADDY_VERSION:?CADDY_VERSION is required}"
  : "${FRP_BIND_PORT:?FRP_BIND_PORT is required}"
  : "${FRP_VHOST_HTTP_PORT:?FRP_VHOST_HTTP_PORT is required}"
  : "${FRP_STATS_USER:?FRP_STATS_USER is required}"
  : "${FRP_STATS_PASSWORD:?FRP_STATS_PASSWORD is required}"
  ALLOW_PORTS="${ALLOW_PORTS:-}"
  # Optional: non-empty means provision Caddy in plain-HTTP (no-TLS) mode,
  # skipping Let's Encrypt and the HTTP->HTTPS redirect. Empty (the default)
  # means the on-demand TLS setup. Only sets the *initial* mode; the box can
  # be toggled afterwards with `wildtunnel-tls-enable`/`wildtunnel-tls-disable`.
  NO_TLS="${NO_TLS:-}"
  DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
  # Generated client-side by the deploying CLI (mirroring frp_token) so it
  # can be printed to the console when `deploy` finishes. Optional: empty
  # means the dashboard falls back to generating its own random password.
  DASHBOARD_INITIAL_ADMIN_PASSWORD="${DASHBOARD_INITIAL_ADMIN_PASSWORD:-}"

  # Persist the non-secret inputs for a later `configure` run.
  mkdir -p /etc/wildtunnel
  chmod 700 /etc/wildtunnel
  cat > "${STATE_FILE}" <<EOF
DOMAIN=${DOMAIN}
DASHBOARD_PORT=${DASHBOARD_PORT}
FRP_VHOST_HTTP_PORT=${FRP_VHOST_HTTP_PORT}
EOF
  chmod 600 "${STATE_FILE}"

  # --- provision phase (first boot only) ---

  # Provider-agnostic: this box may be ARM (EC2 Graviton) or x86_64
  # (Lightsail has no ARM bundles), so the frp/caddy release asset arch
  # is detected at boot rather than assumed.
  case "$(uname -m)" in
    aarch64) ARCH=arm64 ;;
    x86_64) ARCH=amd64 ;;
    *)
      echo "unsupported CPU architecture: $(uname -m)" >&2
      exit 1
      ;;
  esac

  # t4g.nano has 0.5 GiB RAM and no swap by default, which isn't enough for
  # dnf/pip transactions on a fresh box (dnf gets OOM-killed without this).
  # Guarded so a re-run neither fails on an active swap nor duplicates fstab.
  if [[ ! -f /swapfile ]]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  grep -q '^/swapfile ' /proc/swaps || swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

  dnf install -y tar gzip

  # --- frps ---
  curl -fsSL -o /tmp/frp.tar.gz \
    "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${ARCH}.tar.gz"
  mkdir -p /opt/frp
  tar -xzf /tmp/frp.tar.gz -C /opt/frp --strip-components=1
  rm -f /tmp/frp.tar.gz

  cat > /opt/frp/frps.toml <<EOF
bindPort = ${FRP_BIND_PORT}
auth.method = "token"
auth.token = "${FRP_TOKEN}"
vhostHTTPPort = ${FRP_VHOST_HTTP_PORT}
# Stats API for the wildtunnel dashboard (StatsSource) to poll per-tunnel
# connection counts and traffic. Bound to loopback only - never exposed
# to the internet - and gated by basic auth so a local process can't
# read it without credentials.
webServer.addr = "127.0.0.1"
webServer.port = 7500
webServer.user = "${FRP_STATS_USER}"
webServer.password = "${FRP_STATS_PASSWORD}"
EOF
  if [[ -n "${ALLOW_PORTS}" ]]; then
    echo "${ALLOW_PORTS}" >> /opt/frp/frps.toml
  fi
  chmod 600 /opt/frp/frps.toml

  cat > /etc/systemd/system/frps.service <<'UNIT'
[Unit]
Description=frp server
After=network.target

[Service]
ExecStart=/opt/frp/frps -c /opt/frp/frps.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

  systemctl daemon-reload
  systemctl enable --now frps

  # --- wildtunnel dashboard (login-gated admin UI, bound to loopback only;
  # reached solely via Caddy below, so no new security-group/firewall port
  # is needed) ---
  #
  # frps/caddy ship as prebuilt release binaries, but there's no such
  # release for wildtunnel's dashboard extra, so it's installed the normal
  # Python way: a venv plus `pip install` from PyPI. Amazon Linux 2023's
  # *default* `python3` is 3.9, but wildtunnel requires >=3.12 - install the
  # versioned package explicitly rather than getting a too-old interpreter
  # that silently can't find any compatible release on PyPI.
  dnf install -y python3.12 python3.12-pip
  python3.12 -m venv /opt/wildtunnel-dashboard
  /opt/wildtunnel-dashboard/bin/pip install --upgrade pip
  /opt/wildtunnel-dashboard/bin/pip install "wildtunnel[dashboard]"

  mkdir -p /var/lib/wildtunnel-dashboard
  chmod 700 /var/lib/wildtunnel-dashboard

  # The session-signing secret is generated once and persisted across
  # restarts/reprovisions (regenerating it would silently log out every
  # admin session); the bootstrap admin password, by contrast, is handled
  # by wildtunnel.dashboard.bootstrap.ensure_bootstrapped itself, which
  # already only writes it once.
  DASHBOARD_SESSION_SECRET_FILE=/var/lib/wildtunnel-dashboard/session-secret
  if [[ ! -f "${DASHBOARD_SESSION_SECRET_FILE}" ]]; then
    ( umask 077 && /opt/wildtunnel-dashboard/bin/python -c \
      'import secrets; print(secrets.token_hex(32))' \
      > "${DASHBOARD_SESSION_SECRET_FILE}" )
  fi
  chmod 600 "${DASHBOARD_SESSION_SECRET_FILE}"

  cat > /etc/systemd/system/wildtunnel-dashboard.service <<EOF
[Unit]
Description=wildtunnel dashboard
After=network.target

[Service]
Environment=DASHBOARD_DB_PATH=/var/lib/wildtunnel-dashboard/dashboard.db
Environment=DASHBOARD_SECRET_FILE=/var/lib/wildtunnel-dashboard/admin-password.txt
Environment=DASHBOARD_SESSION_SECRET_FILE=${DASHBOARD_SESSION_SECRET_FILE}
Environment=DASHBOARD_PORT=${DASHBOARD_PORT}
Environment=FRP_STATS_USER=${FRP_STATS_USER}
Environment=FRP_STATS_PASSWORD=${FRP_STATS_PASSWORD}
Environment=DASHBOARD_INITIAL_ADMIN_PASSWORD=${DASHBOARD_INITIAL_ADMIN_PASSWORD}
ExecStart=/opt/wildtunnel-dashboard/bin/python -m wildtunnel.dashboard.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now wildtunnel-dashboard

  # --- caddy (reverse proxy + TLS termination) ---
  curl -fsSL -o /tmp/caddy.tar.gz \
    "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${ARCH}.tar.gz"
  tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy
  chmod +x /usr/local/bin/caddy
  rm -f /tmp/caddy.tar.gz

  cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy
After=network.target

[Service]
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
else
  # configure mode: read the non-secret inputs persisted at provision time.
  # shellcheck source=/dev/null
  . "${STATE_FILE}"
fi

# === configure phase (runs on first provision and on upgrade) ===
# Writes the non-secret, version-dependent config and selects the active
# Caddyfile. Idempotent.
#
# The admin executables below are replaced atomically (write a temp file,
# chmod, then rename). This matters for `wildtunnel-upgrade` in particular:
# it runs the configure phase, which rewrites the very script still being
# executed - a plain in-place truncate would corrupt the remaining commands,
# whereas a rename leaves the running shell reading the old inode.

# SSH-based admin password recovery: `sudo wildtunnel-reset-password`
# generates a fresh admin password, forces a change on next login, and
# prints the new password - the only recovery path now that there's no
# email-based forgot-password flow. Runs against its own sqlite
# connection, so it is safe to use without stopping the dashboard
# service first.
cat > /usr/local/bin/wildtunnel-reset-password.tmp <<EOF
#!/bin/bash
set -euo pipefail
export DASHBOARD_DB_PATH=/var/lib/wildtunnel-dashboard/dashboard.db
export DASHBOARD_SECRET_FILE=/var/lib/wildtunnel-dashboard/admin-password.txt
exec /opt/wildtunnel-dashboard/bin/python -m wildtunnel.dashboard.reset_password
EOF
chmod +x /usr/local/bin/wildtunnel-reset-password.tmp
mv /usr/local/bin/wildtunnel-reset-password.tmp /usr/local/bin/wildtunnel-reset-password

# Server-side upgrade: `sudo wildtunnel-upgrade` pulls the latest
# wildtunnel[dashboard] release from PyPI, re-applies the configure phase
# from the freshly-installed package (so shell-level changes reach an
# already-deployed box), then restarts the affected services. It also
# re-writes itself and the other admin executables, so future upgrades stay
# current without a redeploy. frps/Caddy binaries remain provision-time only.
cat > /usr/local/bin/wildtunnel-upgrade.tmp <<'EOF'
#!/bin/bash
set -euo pipefail
VENV_PYTHON=/opt/wildtunnel-dashboard/bin/python
BEFORE="$("${VENV_PYTHON}" -c 'import importlib.metadata as m; print(m.version("wildtunnel"))' 2>/dev/null || echo unknown)"
/opt/wildtunnel-dashboard/bin/pip install --upgrade "wildtunnel[dashboard]"
CONFIGURE_SCRIPT="$("${VENV_PYTHON}" -c 'import importlib.metadata as m; print(m.distribution("wildtunnel").locate_file("wildtunnel/server/assets/server-setup.sh"))')"
bash "${CONFIGURE_SCRIPT}" configure
systemctl restart wildtunnel-dashboard
systemctl reload caddy
AFTER="$("${VENV_PYTHON}" -c 'import importlib.metadata as m; print(m.version("wildtunnel"))' 2>/dev/null || echo unknown)"
echo "upgraded wildtunnel ${BEFORE} -> ${AFTER}"
EOF
chmod +x /usr/local/bin/wildtunnel-upgrade.tmp
mv /usr/local/bin/wildtunnel-upgrade.tmp /usr/local/bin/wildtunnel-upgrade

# Two Caddyfile variants are written so the on-box
# `wildtunnel-tls-enable` / `wildtunnel-tls-disable` executables can swap
# between them later without re-provisioning (deploy never re-runs on a
# live box). The active Caddyfile and the tls-mode state file are seeded
# from NO_TLS on provision, and preserved on a configure re-run.
mkdir -p /etc/caddy

# --- TLS variant: Caddy auto-issues a per-host cert via Let's Encrypt
# HTTP-01 on first request (on-demand). DNS lives on Cloudflare now, so
# there's no DNS-01 challenge and no wildcard cert; each subdomain just
# needs to resolve to this host's public IP (DNS-only, not
# Cloudflare-proxied) with port 80 reachable. Caddy also handles renewal
# itself, so no certbot timer.
#
# Caddy refuses to start with on-demand TLS unless a permission check is
# configured (abuse prevention: without one, anyone could make it request
# certs for arbitrary hostnames and burn the Let's Encrypt rate limit). The
# site block below is already scoped to *.${DOMAIN}, so the check only
# needs to rubber-stamp requests Caddy already restricted by that match.
# The ask site must keep the explicit http:// scheme: without it, Caddy's
# auto-HTTPS wraps 127.0.0.1:9123 in its own (internal-CA) TLS, which the
# plain-http `ask` callback below can't speak to, and cert issuance for
# every real subdomain silently fails.
CADDY_ASK_ADDR="http://127.0.0.1:9123"
cat > /etc/caddy/Caddyfile.tls <<EOF
{
    on_demand_tls {
        ask ${CADDY_ASK_ADDR}/ask
    }
}

${CADDY_ASK_ADDR} {
    respond /ask 200
}

wildtunnel.${DOMAIN} {
    tls {
        on_demand
    }
    reverse_proxy 127.0.0.1:${DASHBOARD_PORT}
}

*.${DOMAIN} {
    tls {
        on_demand
    }
    reverse_proxy 127.0.0.1:${FRP_VHOST_HTTP_PORT}
}

http://wildtunnel.${DOMAIN} {
    redir https://{host}{uri} permanent
}

http://*.${DOMAIN} {
    redir https://{host}{uri} permanent
}
EOF

# --- Plain (no-TLS) variant: serve plain HTTP on :80 only - no Let's
# Encrypt, no redirect. The explicit http:// scheme on each site address
# defeats Caddy's auto-HTTPS, so it never attempts to obtain a cert.
cat > /etc/caddy/Caddyfile.plain <<EOF
http://wildtunnel.${DOMAIN} {
    reverse_proxy 127.0.0.1:${DASHBOARD_PORT}
}

http://*.${DOMAIN} {
    reverse_proxy 127.0.0.1:${FRP_VHOST_HTTP_PORT}
}
EOF

# --- Active config + state file. Seeded from NO_TLS on provision; on a
# configure re-run the existing tls-mode is preserved so an upgrade never
# silently flips a live box. The toggle executables below rewrite both of
# these; the state file is what makes them idempotent.
if [[ "${MODE}" == "provision" ]]; then
  if [[ -n "${NO_TLS}" ]]; then
    TLS_MODE=plain
  else
    TLS_MODE=tls
  fi
  echo "${TLS_MODE}" > /etc/caddy/tls-mode
else
  TLS_MODE="$(cat /etc/caddy/tls-mode 2>/dev/null || echo tls)"
fi
cp "/etc/caddy/Caddyfile.${TLS_MODE}" /etc/caddy/Caddyfile

# --- Toggle executables: swap the active Caddyfile between the two
# variants and reload Caddy. Idempotent, and installed unconditionally so
# a box can be toggled in either direction regardless of its initial mode.
cat > /usr/local/bin/wildtunnel-tls-enable.tmp <<'EOF'
#!/bin/bash
set -euo pipefail
STATE_FILE=/etc/caddy/tls-mode
if [[ "$(cat "${STATE_FILE}" 2>/dev/null || echo plain)" == "tls" ]]; then
  echo "TLS is already enabled"
  exit 0
fi
cp /etc/caddy/Caddyfile.tls /etc/caddy/Caddyfile
# Write the state only after a successful reload; a failed reload must not
# leave the state claiming a mode Caddy never actually applied.
systemctl reload caddy
echo tls > "${STATE_FILE}"
echo "TLS enabled"
EOF
chmod +x /usr/local/bin/wildtunnel-tls-enable.tmp
mv /usr/local/bin/wildtunnel-tls-enable.tmp /usr/local/bin/wildtunnel-tls-enable

cat > /usr/local/bin/wildtunnel-tls-disable.tmp <<'EOF'
#!/bin/bash
set -euo pipefail
STATE_FILE=/etc/caddy/tls-mode
if [[ "$(cat "${STATE_FILE}" 2>/dev/null || echo tls)" == "plain" ]]; then
  echo "TLS is already disabled"
  exit 0
fi
cp /etc/caddy/Caddyfile.plain /etc/caddy/Caddyfile
# Write the state only after a successful reload; a failed reload must not
# leave the state claiming a mode Caddy never actually applied.
systemctl reload caddy
echo plain > "${STATE_FILE}"
echo "TLS disabled"
EOF
chmod +x /usr/local/bin/wildtunnel-tls-disable.tmp
mv /usr/local/bin/wildtunnel-tls-disable.tmp /usr/local/bin/wildtunnel-tls-disable

if [[ "${MODE}" == "provision" ]]; then
  # Enable caddy only after the configure phase has written the active
  # Caddyfile (caddy needs it to start).
  systemctl daemon-reload
  systemctl enable --now caddy
fi
