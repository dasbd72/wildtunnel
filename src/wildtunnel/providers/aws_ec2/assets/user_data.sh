#!/bin/bash
set -euxo pipefail

# AWS-specific prelude: this is the only place in the whole box-bring-up
# path that talks to AWS. It resolves the frp token from SSM, then hands
# off to the generic (provider-agnostic) installer via its env-var
# contract.
#
# User-data runs this early in boot, before the instance metadata
# service reliably hands out the instance role's credentials - a cold
# "NoCredentials" failure here is a boot-time race, not a real auth
# problem, so retry instead of letting `set -e` kill provisioning on
# the first attempt.
MAX_TOKEN_ATTEMPTS=30
TOKEN_RETRY_INTERVAL_SECS=2
for _attempt in $(seq 1 "${MAX_TOKEN_ATTEMPTS}"); do
  if TOKEN=$(aws ssm get-parameter --name "__TOKEN_PARAM__" --with-decryption --region "__REGION__" --query Parameter.Value --output text 2>/tmp/ssm-get-parameter.err); then
    break
  fi
  TOKEN=""
  sleep "${TOKEN_RETRY_INTERVAL_SECS}"
done
if [[ -z "${TOKEN}" ]]; then
  echo "failed to fetch frp token from SSM after retries:" >&2
  cat /tmp/ssm-get-parameter.err >&2
  exit 1
fi

export DOMAIN="__DOMAIN__"
export FRP_TOKEN="${TOKEN}"
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
