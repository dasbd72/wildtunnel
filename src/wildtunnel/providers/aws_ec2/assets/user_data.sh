#!/bin/bash
set -euxo pipefail

# AWS-specific prelude: this is the only place in the whole box-bring-up
# path that talks to AWS. It resolves the frp token from SSM, then hands
# off to the generic (provider-agnostic) installer via its env-var
# contract.
TOKEN=$(aws ssm get-parameter --name "__TOKEN_PARAM__" --with-decryption --region "__REGION__" --query Parameter.Value --output text)

export DOMAIN="__DOMAIN__"
export FRP_TOKEN="${TOKEN}"
export FRP_VERSION="__FRP_VERSION__"
export CADDY_VERSION="__CADDY_VERSION__"
export FRP_BIND_PORT="__BIND_PORT__"
export FRP_VHOST_HTTP_PORT="__VHOST_HTTP_PORT__"
export ALLOW_PORTS="__ALLOW_PORTS__"

cat > /tmp/server-setup.sh <<'SERVER_SETUP_SCRIPT'
__SERVER_SETUP_SCRIPT__
SERVER_SETUP_SCRIPT
chmod +x /tmp/server-setup.sh
/tmp/server-setup.sh
