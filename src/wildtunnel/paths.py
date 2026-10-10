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

# Each provider keeps its own config file. An earlier version had both
# providers share AWS_EC2_CONFIG_FILE (discriminated by a top-level
# "provider" field), on the assumption that only one provider is ever
# deployed at a time - but a user can have an aws-ec2 stack AND an
# aws-lightsail instance deployed simultaneously, and switching the
# "active" provider overwrote the other one's region, breaking its own
# `status`/`destroy`/etc. Separate files let both stay manageable at once.
AWS_LIGHTSAIL_CONFIG_FILE = CONFIG_DIR / "aws-lightsail-config.json"


def other_provider(path: Path, own_provider: str) -> str | None:
    """Return the config file's provider tag if it's for a different provider.

    Each provider now has its own config file, so this should only ever
    trigger if a file was manually copied or edited into the wrong
    provider's shape. Config.load() raises KeyError in that case; this
    distinguishes "this file is someone else's provider" from "this file
    is genuinely corrupt", so the caller can report (or route around)
    each case correctly instead of showing a generic parse error.
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
