from pathlib import Path

import pytest

from wildtunnel.services import Service, load_services, save_services


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_services(tmp_path / "services.json") == {}


def test_round_trips_through_json(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "services.json"
    services = {
        "openwebui": Service(target="192.168.50.10:8080"),
        "wireguard": Service(target="127.0.0.1:51820", proto="udp", remote_port=51820),
    }

    save_services(services, path)
    loaded = load_services(path)

    assert loaded == services


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "services.json"

    save_services({}, path)

    assert path.exists()


def test_load_tolerates_legacy_plain_string_entries(tmp_path: Path) -> None:
    path = tmp_path / "services.json"
    path.write_text('{"openwebui": "192.168.50.10:8080"}')

    loaded = load_services(path)

    assert loaded == {"openwebui": Service(target="192.168.50.10:8080")}


class TestServiceValidation:
    def test_defaults_to_http_with_no_remote_port(self) -> None:
        service = Service(target="192.168.50.10:8080")

        assert service.proto == "http"
        assert service.remote_port is None

    def test_rejects_remote_port_for_http(self) -> None:
        with pytest.raises(ValueError, match="remote_port"):
            Service(target="192.168.50.10:8080", proto="http", remote_port=8080)

    @pytest.mark.parametrize("proto", ["tcp", "udp"])
    def test_requires_remote_port_for_tcp_udp(self, proto: str) -> None:
        with pytest.raises(ValueError, match="remote_port"):
            Service(target="127.0.0.1:51820", proto=proto)

    def test_rejects_unsupported_proto(self) -> None:
        with pytest.raises(ValueError, match="proto"):
            Service(target="127.0.0.1:1234", proto="bogus", remote_port=1234)
