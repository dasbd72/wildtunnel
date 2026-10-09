#!/bin/bash
set -euxo pipefail

DOMAIN="__DOMAIN__"
TOKEN_PARAM="__TOKEN_PARAM__"
BIND_PORT="__BIND_PORT__"
VHOST_HTTP_PORT="__VHOST_HTTP_PORT__"
REGION="__REGION__"
FRP_VERSION="__FRP_VERSION__"
CADDY_VERSION="__CADDY_VERSION__"
ALLOW_PORTS="__ALLOW_PORTS__"

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
  "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_arm64.tar.gz"
mkdir -p /opt/frp
tar -xzf /tmp/frp.tar.gz -C /opt/frp --strip-components=1
rm -f /tmp/frp.tar.gz

TOKEN=$(aws ssm get-parameter --name "${TOKEN_PARAM}" --with-decryption --region "${REGION}" --query Parameter.Value --output text)

cat > /opt/frp/frps.toml <<EOF
bindPort = ${BIND_PORT}
auth.method = "token"
auth.token = "${TOKEN}"
vhostHTTPPort = ${VHOST_HTTP_PORT}
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

# --- caddy (reverse proxy + TLS termination) ---
curl -fsSL -o /tmp/caddy.tar.gz \
  "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_arm64.tar.gz"
tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy
chmod +x /usr/local/bin/caddy
rm -f /tmp/caddy.tar.gz

# --- TLS: Caddy auto-issues a per-host cert via Let's Encrypt HTTP-01 on
# first request (on-demand). DNS lives on Cloudflare now, so there's no
# DNS-01 challenge and no wildcard cert; each subdomain just needs to
# resolve to this host's EIP (DNS-only, not Cloudflare-proxied) with port
# 80 reachable. Caddy also handles renewal itself, so no certbot timer.
mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<EOF
*.${DOMAIN} {
    tls {
        on_demand
    }
    reverse_proxy 127.0.0.1:${VHOST_HTTP_PORT}
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
