from pathlib import Path

import pytest
from click.testing import CliRunner

from wildtunnel import cli, frpc_cli
from wildtunnel.frpc_config import FrpcConfig
from wildtunnel.services import Service, load_services


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(frpc_cli, "FRPC_CONFIG_FILE", tmp_path / "frpc-config.json")
    monkeypatch.setattr(frpc_cli, "SERVICES_FILE", tmp_path / "services.json")
    monkeypatch.setattr(frpc_cli, "FRPC_TOML_FILE", tmp_path / "frpc.toml")
    monkeypatch.setattr(
        frpc_cli, "SYSTEMD_UNIT_FILE", tmp_path / "systemd" / "frpc.service"
    )
    return tmp_path


@pytest.fixture(autouse=True)
def fake_collaborators(monkeypatch: pytest.MonkeyPatch) -> dict:
    recorded: dict = {"subprocess_calls": []}

    monkeypatch.setattr(frpc_cli.shutil, "which", lambda _name: "/usr/bin/frpc")

    def fake_run(args, **kwargs):
        recorded["subprocess_calls"].append(args)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(frpc_cli.subprocess, "run", fake_run)
    return recorded


def _config_args() -> list[str]:
    return [
        "frpc",
        "config",
        "--server-addr",
        "1.2.3.4",
        "--base-domain",
        "dasbd72.com",
        "--token",
        "fake-token",
    ]


def _render_args() -> list[str]:
    return [
        "frpc",
        "render",
        "--server-addr",
        "1.2.3.4",
        "--base-domain",
        "dasbd72.com",
        "--token",
        "fake-token",
    ]


class TestConfigCommand:
    def test_writes_config_and_unit_then_enables_service(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code == 0, result.output
        config = FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE)
        assert config.server_addr == "1.2.3.4"
        assert config.base_domain == "dasbd72.com"
        assert config.token == "fake-token"
        assert frpc_cli.FRPC_TOML_FILE.exists()
        assert "fake-token" in frpc_cli.FRPC_TOML_FILE.read_text()
        assert frpc_cli.SYSTEMD_UNIT_FILE.exists()
        assert "/usr/bin/frpc" in frpc_cli.SYSTEMD_UNIT_FILE.read_text()
        assert ["systemctl", "--user", "daemon-reload"] in fake_collaborators[
            "subprocess_calls"
        ]
        assert [
            "systemctl",
            "--user",
            "enable",
            "--now",
            "frpc.service",
        ] in fake_collaborators["subprocess_calls"]

    def test_config_file_is_chmod_600(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code == 0, result.output
        mode = frpc_cli.FRPC_CONFIG_FILE.stat().st_mode & 0o777
        assert mode == 0o600

    def test_fails_clearly_when_frpc_not_on_path(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(frpc_cli.shutil, "which", lambda _name: None)
        runner = CliRunner()

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code != 0
        assert "frpc not found on PATH" in result.output
        assert not frpc_cli.FRPC_CONFIG_FILE.exists()

    @pytest.mark.parametrize("missing", ["--server-addr", "--base-domain", "--token"])
    def test_requires_server_addr_base_domain_and_token(
        self, isolated_paths: Path, fake_collaborators: dict, missing: str
    ) -> None:
        args = _config_args()
        # Drop the flag and its value.
        index = args.index(missing)
        del args[index : index + 2]
        runner = CliRunner()

        result = runner.invoke(cli.main, args)

        assert result.exit_code != 0
        assert missing in result.output

    def test_frpc_toml_is_chmod_600(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code == 0, result.output
        mode = frpc_cli.FRPC_TOML_FILE.stat().st_mode & 0o777
        assert mode == 0o600

    def test_restarts_service_when_already_active(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())
        fake_collaborators["subprocess_calls"].clear()

        result = runner.invoke(cli.main, [*_config_args(), "--yes"])

        assert result.exit_code == 0, result.output
        assert ["systemctl", "--user", "restart", "frpc.service"] in fake_collaborators[
            "subprocess_calls"
        ]

    def test_does_not_wipe_existing_services(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())
        runner.invoke(cli.main, ["frpc", "add", "openwebui", "192.168.50.10:8080"])

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code == 0, result.output
        assert load_services(frpc_cli.SERVICES_FILE) == {
            "openwebui": Service(target="192.168.50.10:8080")
        }

    def test_service_flags_replace_persisted_services(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())
        runner.invoke(cli.main, ["frpc", "add", "old", "192.168.50.10:1111"])

        result = runner.invoke(
            cli.main, [*_config_args(), "--yes", "--service", "new=192.168.50.10:2222"]
        )

        assert result.exit_code == 0, result.output
        assert load_services(frpc_cli.SERVICES_FILE) == {
            "new": Service(target="192.168.50.10:2222")
        }

    def test_prompts_before_overwriting_changed_values(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--server-addr",
                "9.9.9.9",
                "--base-domain",
                "dasbd72.com",
                "--token",
                "fake-token",
            ],
            input="n\n",
        )

        assert result.exit_code != 0
        assert FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE).server_addr == "1.2.3.4"

    def test_overwrites_when_confirmed(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--server-addr",
                "9.9.9.9",
                "--base-domain",
                "dasbd72.com",
                "--token",
                "fake-token",
            ],
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE).server_addr == "9.9.9.9"

    def test_yes_flag_skips_confirmation(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--server-addr",
                "9.9.9.9",
                "--base-domain",
                "dasbd72.com",
                "--token",
                "fake-token",
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output
        assert FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE).server_addr == "9.9.9.9"

    def test_no_prompt_when_values_unchanged(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()
        runner.invoke(cli.main, _config_args())

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code == 0, result.output


class TestRenderCommand:
    def test_renders_from_explicit_flags_only(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "render",
                "--server-addr",
                "1.2.3.4",
                "--base-domain",
                "dasbd72.com",
                "--token",
                "explicit-token",
            ],
        )

        assert result.exit_code == 0, result.output
        assert 'serverAddr = "1.2.3.4"' in result.output
        assert 'auth.token = "explicit-token"' in result.output

    def test_render_persists_nothing(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        runner.invoke(cli.main, _render_args())

        assert not frpc_cli.FRPC_CONFIG_FILE.exists()
        assert not frpc_cli.SERVICES_FILE.exists()
        assert not frpc_cli.FRPC_TOML_FILE.exists()
        assert not frpc_cli.SYSTEMD_UNIT_FILE.exists()
        assert fake_collaborators["subprocess_calls"] == []

    def test_requires_token(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "render",
                "--server-addr",
                "1.2.3.4",
                "--base-domain",
                "dasbd72.com",
            ],
        )

        assert result.exit_code != 0
        assert "--token" in result.output

    def test_renders_proxy_block_from_service_flag(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [*_render_args(), "--service", "openwebui=192.168.50.10:8080"],
        )

        assert result.exit_code == 0, result.output
        assert 'name = "openwebui"' in result.output
        assert 'customDomains = ["openwebui.dasbd72.com"]' in result.output

    def test_renders_udp_proxy_from_extended_service_flag(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [*_render_args(), "--service", "wireguard=127.0.0.1:51820/udp:51820"],
        )

        assert result.exit_code == 0, result.output
        assert 'name = "wireguard"' in result.output
        assert 'type = "udp"' in result.output
        assert "remotePort = 51820" in result.output
        assert "customDomains" not in result.output

    def test_rejects_extended_service_flag_with_non_numeric_remote_port(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [*_render_args(), "--service", "wireguard=127.0.0.1:51820/udp:not-a-port"],
        )

        assert result.exit_code != 0
        assert "--service" in result.output

    def test_rejects_malformed_service_flag(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [*_render_args(), "--service", "openwebui-no-target"],
        )

        assert result.exit_code != 0
        assert "--service" in result.output


@pytest.mark.parametrize(
    "bad_target", ["192.168.50.10", ":8080", "192.168.50.10:", "host:abc"]
)
def test_add_rejects_malformed_target(
    isolated_paths: Path, fake_collaborators: dict, bad_target: str
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(cli.main, ["frpc", "add", "openwebui", bad_target])

    assert result.exit_code != 0
    assert "host:port" in result.output


def test_add_requires_prior_config(isolated_paths: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(cli.main, ["frpc", "add", "openwebui", "192.168.50.10:8080"])

    assert result.exit_code != 0
    assert "wildtunnel frpc config" in result.output


def test_add_registers_service_and_restarts(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(cli.main, ["frpc", "add", "openwebui", "192.168.50.10:8080"])

    assert result.exit_code == 0, result.output
    assert load_services(frpc_cli.SERVICES_FILE) == {
        "openwebui": Service(target="192.168.50.10:8080")
    }
    assert (
        'customDomains = ["openwebui.dasbd72.com"]'
        in frpc_cli.FRPC_TOML_FILE.read_text()
    )
    assert ["systemctl", "--user", "restart", "frpc.service"] in fake_collaborators[
        "subprocess_calls"
    ]


@pytest.mark.parametrize("proto", ["tcp", "udp"])
def test_add_with_remote_port_creates_tcp_or_udp_proxy(
    isolated_paths: Path, fake_collaborators: dict, proto: str
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(
        cli.main,
        [
            "frpc",
            "add",
            "wireguard",
            "127.0.0.1:51820",
            "--proto",
            proto,
            "--remote-port",
            "51820",
        ],
    )

    assert result.exit_code == 0, result.output
    assert load_services(frpc_cli.SERVICES_FILE) == {
        "wireguard": Service(target="127.0.0.1:51820", proto=proto, remote_port=51820)
    }
    toml_text = frpc_cli.FRPC_TOML_FILE.read_text()
    assert f'type = "{proto}"' in toml_text
    assert "remotePort = 51820" in toml_text


def test_add_requires_remote_port_for_tcp_udp_proto(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(
        cli.main, ["frpc", "add", "wireguard", "127.0.0.1:51820", "--proto", "udp"]
    )

    assert result.exit_code != 0
    assert "remote_port" in result.output


def test_add_rejects_remote_port_with_default_http_proto(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(
        cli.main,
        ["frpc", "add", "openwebui", "192.168.50.10:8080", "--remote-port", "8080"],
    )

    assert result.exit_code != 0
    assert "remote_port" in result.output


def test_del_errors_when_service_unknown(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(cli.main, ["frpc", "del", "nonexistent"])

    assert result.exit_code != 0
    assert "nonexistent" in result.output


def test_del_deletes_registered_service(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())
    runner.invoke(cli.main, ["frpc", "add", "openwebui", "192.168.50.10:8080"])

    result = runner.invoke(cli.main, ["frpc", "del", "openwebui"])

    assert result.exit_code == 0, result.output
    assert load_services(frpc_cli.SERVICES_FILE) == {}


def test_list_reports_no_services_when_empty(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())

    result = runner.invoke(cli.main, ["frpc", "list"])

    assert result.exit_code == 0
    assert "no services configured" in result.output


def test_list_shows_configured_services(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())
    runner.invoke(cli.main, ["frpc", "add", "openwebui", "192.168.50.10:8080"])

    result = runner.invoke(cli.main, ["frpc", "list"])

    assert "openwebui.dasbd72.com -> 192.168.50.10:8080" in result.output


def test_list_shows_remote_port_for_non_http_services(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())
    runner.invoke(
        cli.main,
        [
            "frpc",
            "add",
            "wireguard",
            "127.0.0.1:51820",
            "--proto",
            "udp",
            "--remote-port",
            "51820",
        ],
    )

    result = runner.invoke(cli.main, ["frpc", "list"])

    assert "1.2.3.4:51820 (udp) -> 127.0.0.1:51820" in result.output


def test_status_invokes_systemctl_status(
    isolated_paths: Path, fake_collaborators: dict
) -> None:
    runner = CliRunner()

    result = runner.invoke(cli.main, ["frpc", "status"])

    assert result.exit_code == 0
    assert ["systemctl", "--user", "status", "frpc.service"] in fake_collaborators[
        "subprocess_calls"
    ]
