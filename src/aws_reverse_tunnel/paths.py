from __future__ import annotations

import os
from pathlib import Path


def _xdg_dir(env_var: str, default: Path) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else default


XDG_CONFIG_HOME = _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config")
XDG_CACHE_HOME = _xdg_dir("XDG_CACHE_HOME", Path.home() / ".cache")

CONFIG_DIR = XDG_CONFIG_HOME / "aws-reverse-tunnel"
FRPC_CONFIG_FILE = CONFIG_DIR / "frpc-config.json"
SERVICES_FILE = CONFIG_DIR / "services.json"
FRPC_TOML_FILE = CONFIG_DIR / "frpc.toml"

# Real state: losing AWS_EC2_CONFIG_FILE means re-supplying deploy flags;
# losing FRPC_CONFIG_FILE's cached token, or FRP_TOKEN_FILE, rotates/loses
# the token and breaks every frpc client until re-`config`ured. All three
# live alongside each other, not in a cache.
AWS_EC2_CONFIG_FILE = CONFIG_DIR / "config.json"
FRP_TOKEN_FILE = CONFIG_DIR / "frp-token"

# Disposable CDK build/cache artifacts (cdk.out, VPC-lookup context cache).
AWS_EC2_CACHE_DIR = XDG_CACHE_HOME / "aws-reverse-tunnel" / "aws_ec2"

# systemd searches $XDG_CONFIG_HOME/systemd/user for user units.
SYSTEMD_USER_DIR = XDG_CONFIG_HOME / "systemd" / "user"
SYSTEMD_UNIT_FILE = SYSTEMD_USER_DIR / "frpc.service"
