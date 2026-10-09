from pathlib import Path

from aws_reverse_tunnel.infra_config import InfraConfig


def test_round_trips_through_json(tmp_path: Path) -> None:
    config = InfraConfig(domain="dasbd72.com", region="ap-northeast-1")
    path = tmp_path / "nested" / "infra-config.json"

    config.save(path)
    loaded = InfraConfig.load(path)

    assert loaded == config


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    config = InfraConfig(domain="dasbd72.com", region="ap-northeast-1")
    path = tmp_path / "a" / "b" / "infra-config.json"

    config.save(path)

    assert path.exists()


def test_extra_port_range_defaults_to_none() -> None:
    config = InfraConfig(domain="dasbd72.com", region="ap-northeast-1")

    assert config.extra_port_range_start is None
    assert config.extra_port_range_end is None


def test_round_trips_extra_port_range(tmp_path: Path) -> None:
    config = InfraConfig(
        domain="dasbd72.com",
        region="ap-northeast-1",
        extra_port_range_start=20000,
        extra_port_range_end=20100,
    )
    path = tmp_path / "infra-config.json"

    config.save(path)
    loaded = InfraConfig.load(path)

    assert loaded == config


def test_loads_legacy_file_missing_extra_port_range_keys(tmp_path: Path) -> None:
    path = tmp_path / "infra-config.json"
    path.write_text('{"domain": "dasbd72.com", "region": "ap-northeast-1"}')

    loaded = InfraConfig.load(path)

    assert loaded.extra_port_range_start is None
    assert loaded.extra_port_range_end is None


def test_loads_legacy_file_with_removed_hosted_zone_id_key(tmp_path: Path) -> None:
    path = tmp_path / "infra-config.json"
    path.write_text(
        '{"domain": "dasbd72.com", "hosted_zone_id": "Z123", "region": "ap-northeast-1"}'
    )

    loaded = InfraConfig.load(path)

    assert loaded == InfraConfig(domain="dasbd72.com", region="ap-northeast-1")
