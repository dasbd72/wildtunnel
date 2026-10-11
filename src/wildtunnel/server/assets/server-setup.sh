#!/bin/bash
set -euxo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${FRP_TOKEN:?FRP_TOKEN is required}"
: "${FRP_VERSION:?FRP_VERSION is required}"
: "${CADDY_VERSION:?CADDY_VERSION is required}"
: "${FRP_BIND_PORT:?FRP_BIND_PORT is required}"
: "${FRP_VHOST_HTTP_PORT:?FRP_VHOST_HTTP_PORT is required}"
: "${FRP_STATS_USER:?FRP_STATS_USER is required}"
: "${FRP_STATS_PASSWORD:?FRP_STATS_PASSWORD is required}"
ALLOW_PORTS="${ALLOW_PORTS:-}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
# SMTP relay settings are optional: the dashboard's forgot-password email
# flow only needs them once an admin has bound a recovery address, so
# provisioning must not fail without them configured yet.
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-}"
SMTP_USERNAME="${SMTP_USERNAME:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
# Generated client-side by the deploying CLI (mirroring frp_token) so it
# can be printed to the console when `deploy` finishes. Optional: empty
# means the dashboard falls back to generating its own random password.
DASHBOARD_INITIAL_ADMIN_PASSWORD="${DASHBOARD_INITIAL_ADMIN_PASSWORD:-}"

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
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

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
Environment=SMTP_HOST=${SMTP_HOST}
Environment=SMTP_PORT=${SMTP_PORT}
Environment=SMTP_USERNAME=${SMTP_USERNAME}
Environment=SMTP_PASSWORD=${SMTP_PASSWORD}
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

# --- TLS: Caddy auto-issues a per-host cert via Let's Encrypt HTTP-01 on
# first request (on-demand). DNS lives on Cloudflare now, so there's no
# DNS-01 challenge and no wildcard cert; each subdomain just needs to
# resolve to this host's public IP (DNS-only, not Cloudflare-proxied) with
# port 80 reachable. Caddy also handles renewal itself, so no certbot timer.
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
mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<EOF
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

systemctl daemon-reload
systemctl enable --now caddy
