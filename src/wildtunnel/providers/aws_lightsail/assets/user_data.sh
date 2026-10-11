#!/bin/bash
set -euxo pipefail

# Lightsail-specific prelude: this is the only place in the whole
# box-bring-up path that's Lightsail-specific. Unlike aws_ec2's
# user_data.sh, there's no SSM parameter fetch here - Lightsail instances
# have no attachable IAM role, so the frp token is baked directly into
# this script by the CLI at deploy time instead (see cli.py's
# deploy_instance). It then hands off to the generic (provider-agnostic)
# installer via its env-var contract.
export DOMAIN="__DOMAIN__"
export FRP_TOKEN="__FRP_TOKEN__"
export FRP_VERSION="__FRP_VERSION__"
export CADDY_VERSION="__CADDY_VERSION__"
export FRP_BIND_PORT="__BIND_PORT__"
export FRP_VHOST_HTTP_PORT="__VHOST_HTTP_PORT__"
export ALLOW_PORTS="__ALLOW_PORTS__"
export FRP_STATS_USER="__FRP_STATS_USER__"
export FRP_STATS_PASSWORD="__FRP_STATS_PASSWORD__"
export DASHBOARD_INITIAL_ADMIN_PASSWORD="__DASHBOARD_ADMIN_PASSWORD__"

cat > /tmp/server-setup.sh <<'SERVER_SETUP_SCRIPT'
__SERVER_SETUP_SCRIPT__
SERVER_SETUP_SCRIPT
chmod +x /tmp/server-setup.sh
/tmp/server-setup.sh
