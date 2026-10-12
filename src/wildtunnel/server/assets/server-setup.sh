#!/bin/bash
set -euo pipefail

# provision (default): first boot. configure: idempotent re-apply for upgrades.
MODE="${1:-provision}"
# Non-secret inputs configure needs to rebuild the Caddyfile. Secrets are
# never stored here - they live only in their 0600 files.
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
  NO_TLS="${NO_TLS:-}"  # non-empty = plain HTTP, no Let's Encrypt
  DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
  DASHBOARD_INITIAL_ADMIN_PASSWORD="${DASHBOARD_INITIAL_ADMIN_PASSWORD:-}"

  mkdir -p /etc/wildtunnel
  chmod 700 /etc/wildtunnel
  cat > "${STATE_FILE}" <<EOF
DOMAIN=${DOMAIN}
DASHBOARD_PORT=${DASHBOARD_PORT}
FRP_VHOST_HTTP_PORT=${FRP_VHOST_HTTP_PORT}
EOF
  chmod 600 "${STATE_FILE}"

  # --- provision (first boot only) ---

  # EC2 Graviton is ARM; Lightsail has no ARM bundles. Detect, don't assume.
  case "$(uname -m)" in
    aarch64) ARCH=arm64 ;;
    x86_64) ARCH=amd64 ;;
    *) echo "unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
  esac

  # t4g.nano has 0.5 GiB RAM, no swap; dnf/pip get OOM-killed without it.
  if [[ ! -f /swapfile ]]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  grep -q '^/swapfile ' /proc/swaps || swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

  dnf install -y tar gzip

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
# stats API, loopback-only + basic auth (the dashboard polls it)
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

  # Dashboard: loopback-only, reached solely via Caddy. AL2023's default
  # python3 is 3.9; wildtunnel needs >=3.12, so install the versioned pkg.
  dnf install -y python3.12 python3.12-pip
  python3.12 -m venv /opt/wildtunnel-dashboard
  /opt/wildtunnel-dashboard/bin/pip install --upgrade pip
  /opt/wildtunnel-dashboard/bin/pip install "wildtunnel[dashboard]"

  mkdir -p /var/lib/wildtunnel-dashboard
  chmod 700 /var/lib/wildtunnel-dashboard

  # Session secret generated once and persisted (regenerating logs everyone out).
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
  # shellcheck source=/dev/null
  . "${STATE_FILE}"
fi

# === configure phase (runs on first provision and on upgrade) ===
# Non-secret, version-dependent config. Idempotent. Admin executables are
# replaced atomically so wildtunnel-upgrade (which rewrites itself) is never
# truncated mid-run.

cat > /usr/local/bin/wildtunnel-reset-password.tmp <<EOF
#!/bin/bash
set -euo pipefail
export DASHBOARD_DB_PATH=/var/lib/wildtunnel-dashboard/dashboard.db
export DASHBOARD_SECRET_FILE=/var/lib/wildtunnel-dashboard/admin-password.txt
exec /opt/wildtunnel-dashboard/bin/python -m wildtunnel.dashboard.reset_password
EOF
chmod +x /usr/local/bin/wildtunnel-reset-password.tmp
mv /usr/local/bin/wildtunnel-reset-password.tmp /usr/local/bin/wildtunnel-reset-password

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

mkdir -p /etc/caddy

# TLS variant: on-demand Let's Encrypt per host. The ask endpoint is the
# permission check Caddy requires for on-demand TLS; it must keep the
# explicit http:// scheme or cert issuance silently fails.
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

# Plain (no-TLS) variant: explicit http:// scheme defeats Caddy auto-HTTPS.
cat > /etc/caddy/Caddyfile.plain <<EOF
http://wildtunnel.${DOMAIN} {
    reverse_proxy 127.0.0.1:${DASHBOARD_PORT}
}

http://*.${DOMAIN} {
    reverse_proxy 127.0.0.1:${FRP_VHOST_HTTP_PORT}
}
EOF

# --- Active config + state file. Seeded from NO_TLS on provision; preserved
# on a configure re-run so an upgrade never silently flips a live box.
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

cat > /usr/local/bin/wildtunnel-tls-enable.tmp <<'EOF'
#!/bin/bash
set -euo pipefail
STATE_FILE=/etc/caddy/tls-mode
if [[ "$(cat "${STATE_FILE}" 2>/dev/null || echo plain)" == "tls" ]]; then
  echo "TLS is already enabled"
  exit 0
fi
cp /etc/caddy/Caddyfile.tls /etc/caddy/Caddyfile
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
systemctl reload caddy
echo plain > "${STATE_FILE}"
echo "TLS disabled"
EOF
chmod +x /usr/local/bin/wildtunnel-tls-disable.tmp
mv /usr/local/bin/wildtunnel-tls-disable.tmp /usr/local/bin/wildtunnel-tls-disable

if [[ "${MODE}" == "provision" ]]; then
  systemctl daemon-reload
  systemctl enable --now caddy
fi
