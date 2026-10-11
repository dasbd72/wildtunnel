import pytest

from wildtunnel.frpc_config import FrpcConfig
from wildtunnel.frpc_toml import parse_target, render
from wildtunnel.services import Service


class TestParseTarget:
    def test_splits_host_and_port(self) -> None:
        assert parse_target("192.168.50.10:8080") == ("192.168.50.10", 8080)

    @pytest.mark.parametrize(
        "target", ["192.168.50.10", ":8080", "192.168.50.10:", "host:abc"]
    )
    def test_rejects_malformed_target(self, target: str) -> None:
        with pytest.raises(ValueError, match="invalid target"):
            parse_target(target)


def test_renders_server_and_auth_settings() -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4",
        base_domain="example.com",
        token="secret-token",
        server_port=7000,
    )

    output = render(config, {})

    assert 'serverAddr = "1.2.3.4"' in output
    assert "serverPort = 7000" in output
    assert 'auth.token = "secret-token"' in output


def test_renders_one_proxy_block_per_service() -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret-token"
    )
    services = {"openwebui": Service(target="192.168.50.10:8080")}

    output = render(config, services)

    assert "[[proxies]]" in output
    assert 'name = "openwebui"' in output
    assert 'type = "http"' in output
    assert 'localIP = "192.168.50.10"' in output
    assert "localPort = 8080" in output
    assert 'customDomains = ["openwebui.example.com"]' in output


def test_renders_no_proxy_blocks_when_no_services() -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret-token"
    )

    output = render(config, {})

    assert "[[proxies]]" not in output


def test_renders_services_in_sorted_order_for_determinism() -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret-token"
    )
    services = {
        "zeta": Service(target="10.0.0.1:1"),
        "alpha": Service(target="10.0.0.2:2"),
    }

    output = render(config, services)

    assert output.index('name = "alpha"') < output.index('name = "zeta"')


@pytest.mark.parametrize("proto", ["tcp", "udp"])
def test_renders_tcp_udp_proxy_with_remote_port(proto: str) -> None:
    config = FrpcConfig(
        server_addr="1.2.3.4", base_domain="example.com", token="secret-token"
    )
    services = {
        "wireguard": Service(target="127.0.0.1:51820", proto=proto, remote_port=51820)
    }

    output = render(config, services)

    assert f'type = "{proto}"' in output
    assert "remotePort = 51820" in output
    assert "customDomains" not in output
