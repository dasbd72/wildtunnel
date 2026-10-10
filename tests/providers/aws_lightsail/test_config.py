import re
from pathlib import Path

from wildtunnel.providers.aws_lightsail.config import (
    AwsLightsailConfig,
    diff_ports,
    port_infos,
    render_user_data,
)


def test_round_trips_through_json(tmp_path: Path) -> None:
    config = AwsLightsailConfig(domain="dasbd72.com", region="ap-northeast-1")
    path = tmp_path / "nested" / "config.json"

    config.save(path)
    loaded = AwsLightsailConfig.load(path)

    assert loaded == config


def test_loads_file_missing_extra_port_range_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"provider": "aws-lightsail", "domain": "dasbd72.com", '
        '"aws_lightsail": {"region": "ap-northeast-1"}}'
    )

    loaded = AwsLightsailConfig.load(path)

    assert loaded == AwsLightsailConfig(domain="dasbd72.com", region="ap-northeast-1")


def test_port_infos_without_extra_range_opens_only_http_https_and_frp() -> None:
    infos = port_infos(7000, None)

    assert {
        "fromPort": 80,
        "toPort": 80,
        "protocol": "tcp",
        "cidrs": ["0.0.0.0/0"],
    } in infos
    assert {
        "fromPort": 443,
        "toPort": 443,
        "protocol": "tcp",
        "cidrs": ["0.0.0.0/0"],
    } in infos
    assert {
        "fromPort": 7000,
        "toPort": 7000,
        "protocol": "tcp",
        "cidrs": ["0.0.0.0/0"],
    } in infos
    assert not any(info["protocol"] == "udp" for info in infos)


def test_port_infos_with_extra_range_opens_tcp_and_udp_for_that_range() -> None:
    infos = port_infos(7000, (24454, 25565))

    assert {
        "fromPort": 24454,
        "toPort": 25565,
        "protocol": "tcp",
        "cidrs": ["0.0.0.0/0"],
    } in infos
    assert {
        "fromPort": 24454,
        "toPort": 25565,
        "protocol": "udp",
        "cidrs": ["0.0.0.0/0"],
    } in infos


def test_render_user_data_embeds_token_domain_and_server_setup_script() -> None:
    script = render_user_data(
        domain="dasbd72.com",
        frp_token="s3cret-token",
        frp_bind_port=7000,
        frp_vhost_http_port=7080,
        frp_version="0.71.0",
        caddy_version="2.9.1",
        extra_port_range=None,
    )

    assert 'DOMAIN="dasbd72.com"' in script
    assert 'FRP_TOKEN="s3cret-token"' in script
    assert 'FRP_BIND_PORT="7000"' in script
    assert 'FRP_VHOST_HTTP_PORT="7080"' in script
    # The generic installer's own content must be embedded verbatim.
    assert "mkswap /swapfile" in script
    assert not re.search(r"__[A-Z_]+__", script)  # no leftover placeholders


def test_render_user_data_sets_allow_ports_when_extra_range_given() -> None:
    script = render_user_data(
        domain="dasbd72.com",
        frp_token="s3cret-token",
        frp_bind_port=7000,
        frp_vhost_http_port=7080,
        frp_version="0.71.0",
        caddy_version="2.9.1",
        extra_port_range=(24454, 25565),
    )

    assert "allowPorts = [{ start = 24454, end = 25565 }]" in script


def test_diff_ports_reports_desired_rule_missing_from_current() -> None:
    desired = [
        {"fromPort": 443, "toPort": 443, "protocol": "tcp", "cidrs": ["0.0.0.0/0"]}
    ]
    current: list[dict] = []

    to_open, to_close = diff_ports(desired, current)

    assert to_open == desired
    assert to_close == []


def test_diff_ports_reports_stale_rule_not_in_desired() -> None:
    desired: list[dict] = []
    current = [
        {
            "fromPort": 51820,
            "toPort": 51820,
            "protocol": "udp",
            "state": "open",
            "cidrs": ["0.0.0.0/0"],
        }
    ]

    to_open, to_close = diff_ports(desired, current)

    assert to_open == []
    assert to_close == current


def test_diff_ports_reports_nothing_when_already_in_sync() -> None:
    rule = {
        "fromPort": 443,
        "toPort": 443,
        "protocol": "tcp",
        "state": "open",
        "cidrs": ["0.0.0.0/0"],
    }

    to_open, to_close = diff_ports([rule], [rule])

    assert to_open == []
    assert to_close == []
