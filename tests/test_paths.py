from pathlib import Path

import pytest

from wildtunnel import paths
from wildtunnel.paths import _xdg_dir


def test_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/config")

    assert _xdg_dir("XDG_CONFIG_HOME", Path("/default")) == Path("/custom/config")


def test_falls_back_to_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert _xdg_dir("XDG_CONFIG_HOME", Path("/default")) == Path("/default")


def test_falls_back_to_default_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per the XDG Base Directory spec, an empty value is treated as unset.
    monkeypatch.setenv("XDG_CONFIG_HOME", "")

    assert _xdg_dir("XDG_CONFIG_HOME", Path("/default")) == Path("/default")


def test_each_provider_has_its_own_config_file() -> None:
    # Regression: these two used to alias the same path, discriminated by
    # a top-level "provider" field on disk. That broke the moment someone
    # had both an aws-ec2 stack and an aws-lightsail instance deployed at
    # once - configuring one provider overwrote the other's region,
    # breaking that provider's own status/destroy/etc.
    assert paths.AWS_EC2_CONFIG_FILE != paths.AWS_LIGHTSAIL_CONFIG_FILE
