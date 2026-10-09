import json
from pathlib import Path

from aws_reverse_tunnel.frpc_config import FrpcConfig


def test_round_trips_through_json(tmp_path: Path) -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret"
    )
    path = tmp_path / "nested" / "frpc-config.json"

    config.save(path)
    loaded = FrpcConfig.load(path)

    assert loaded == config


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret"
    )
    path = tmp_path / "a" / "b" / "frpc-config.json"

    config.save(path)

    assert path.exists()


def test_defaults() -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret"
    )

    assert config.server_port == 7000


def test_load_ignores_unknown_legacy_keys(tmp_path: Path) -> None:
    # A settings file written before region/token_param were dropped.
    path = tmp_path / "frpc-config.json"
    path.write_text(
        json.dumps(
            {
                "server_addr": "1.2.3.4",
                "base_domain": "example.com",
                "token": "secret",
                "token_param": "/old/path",
                "region": "ap-northeast-1",
            }
        )
    )

    loaded = FrpcConfig.load(path)

    assert loaded == FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret"
    )


def test_token_excluded_from_repr_and_str() -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="super-secret-value"
    )

    assert "super-secret-value" not in repr(config)
    assert "super-secret-value" not in str(config)
