from pathlib import Path

from wildtunnel.providers.aws_ec2.config import AwsEc2Config


def test_round_trips_through_json(tmp_path: Path) -> None:
    config = AwsEc2Config(domain="example.com", region="ap-northeast-1")
    path = tmp_path / "nested" / "config.json"

    config.save(path)
    loaded = AwsEc2Config.load(path)

    assert loaded == config


def test_loads_file_missing_extra_port_range_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"provider": "aws-ec2", "domain": "example.com", '
        '"aws_ec2": {"region": "ap-northeast-1"}}'
    )

    loaded = AwsEc2Config.load(path)

    assert loaded == AwsEc2Config(domain="example.com", region="ap-northeast-1")


def test_loads_file_with_unknown_top_level_and_nested_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"provider": "aws-ec2", "domain": "example.com", "hosted_zone_id": "Z123", '
        '"aws_ec2": {"region": "ap-northeast-1", "account_id": "999"}}'
    )

    loaded = AwsEc2Config.load(path)

    assert loaded == AwsEc2Config(domain="example.com", region="ap-northeast-1")


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    config = AwsEc2Config(domain="example.com", region="ap-northeast-1")
    path = tmp_path / "a" / "b" / "config.json"

    config.save(path)

    assert path.exists()


def test_extra_port_range_defaults_to_none() -> None:
    config = AwsEc2Config(domain="example.com", region="ap-northeast-1")

    assert config.extra_port_range is None


def test_round_trips_extra_port_range(tmp_path: Path) -> None:
    config = AwsEc2Config(
        domain="example.com", region="ap-northeast-1", extra_port_range=(20000, 20100)
    )
    path = tmp_path / "config.json"

    config.save(path)
    loaded = AwsEc2Config.load(path)

    assert loaded == config


def test_no_tls_defaults_to_false() -> None:
    config = AwsEc2Config(domain="example.com", region="ap-northeast-1")

    assert config.no_tls is False


def test_round_trips_no_tls(tmp_path: Path) -> None:
    config = AwsEc2Config(domain="example.com", region="ap-northeast-1", no_tls=True)
    path = tmp_path / "config.json"

    config.save(path)
    loaded = AwsEc2Config.load(path)

    assert loaded == config


def test_loads_file_missing_no_tls_key_as_false(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"provider": "aws-ec2", "domain": "example.com", '
        '"extra_port_range": null, "aws_ec2": {"region": "ap-northeast-1"}}'
    )

    loaded = AwsEc2Config.load(path)

    assert loaded.no_tls is False
