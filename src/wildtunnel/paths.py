from __future__ import annotations

import json
import os
from pathlib import Path


def _xdg_dir(env_var: str, default: Path) -> Path:
    value = os.environ.get(env_var)
    return Path(value) if value else default


XDG_CONFIG_HOME = _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config")
XDG_CACHE_HOME = _xdg_dir("XDG_CACHE_HOME", Path.home() / ".cache")

CONFIG_DIR = XDG_CONFIG_HOME / "wildtunnel"
FRPC_CONFIG_FILE = CONFIG_DIR / "frpc-config.json"
SERVICES_FILE = CONFIG_DIR / "services.json"
FRPC_TOML_FILE = CONFIG_DIR / "frpc.toml"

# Real state: losing AWS_EC2_CONFIG_FILE means re-supplying deploy flags;
# losing FRPC_CONFIG_FILE's cached token, or FRP_TOKEN_FILE, rotates/loses
# the token and breaks every frpc client until re-`config`ured. All three
# live alongside each other, not in a cache.
AWS_EC2_CONFIG_FILE = CONFIG_DIR / "config.json"
FRP_TOKEN_FILE = CONFIG_DIR / "frp-token"

# aws_lightsail deploys via direct boto3 calls (no CDK/CloudFormation), so
# it has no stack-update cycle and reuses this same config.json - only one
# provider is configured at a time, discriminated by the top-level
# "provider" field - rather than needing its own file.
AWS_LIGHTSAIL_CONFIG_FILE = AWS_EC2_CONFIG_FILE


def other_provider(path: Path, own_provider: str) -> str | None:
    """Return the config file's provider tag if it's for a different provider.

    Every provider's Config.load() raises KeyError when config.json
    currently belongs to a different provider (they share the one file
    above). Each provider's CLI uses this to distinguish "this file is
    someone else's provider" from "this file is genuinely corrupt", so it
    can report (or route around) each case correctly.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    provider = data.get("provider")
    return provider if isinstance(provider, str) and provider != own_provider else None


# Disposable CDK build/cache artifacts (cdk.out, VPC-lookup context cache).
AWS_EC2_CACHE_DIR = XDG_CACHE_HOME / "wildtunnel" / "aws_ec2"

# systemd searches $XDG_CONFIG_HOME/systemd/user for user units.
SYSTEMD_USER_DIR = XDG_CONFIG_HOME / "systemd" / "user"
SYSTEMD_UNIT_FILE = SYSTEMD_USER_DIR / "frpc.service"
