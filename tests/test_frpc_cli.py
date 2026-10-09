from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from aws_reverse_tunnel import cli, frpc_cli
from aws_reverse_tunnel.frpc_config import FrpcConfig
from aws_reverse_tunnel.infra_config import InfraConfig
from aws_reverse_tunnel.services import Service, load_services


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(frpc_cli, "FRPC_CONFIG_FILE", tmp_path / "frpc-config.json")
    monkeypatch.setattr(frpc_cli, "SERVICES_FILE", tmp_path / "services.json")
    monkeypatch.setattr(frpc_cli, "FRPC_TOML_FILE", tmp_path / "frpc.toml")
    monkeypatch.setattr(
        frpc_cli, "SYSTEMD_UNIT_FILE", tmp_path / "systemd" / "frpc.service"
    )
    monkeypatch.setattr(frpc_cli, "INFRA_CONFIG_FILE", tmp_path / "infra-config.json")
    return tmp_path


def _stack_not_found(_region: str) -> dict:
    raise ClientError(
        {"Error": {"Code": "ValidationError", "Message": "does not exist"}},
        "DescribeStacks",
    )


def _param_not_found(_param: str, _region: str) -> str:
    raise ClientError(
        {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
        "GetParameter",
    )


@pytest.fixture(autouse=True)
def fake_collaborators(monkeypatch: pytest.MonkeyPatch) -> dict:
    recorded: dict = {"subprocess_calls": []}

    monkeypatch.setattr(frpc_cli.shutil, "which", lambda _name: "/usr/bin/frpc")
    monkeypatch.setattr(
        frpc_cli.token_module, "fetch_token", lambda _param, _region: "fake-token"
    )
    monkeypatch.setattr(
        frpc_cli,
        "describe_stack",
        lambda _region: {
            "Outputs": [{"OutputKey": "ElasticIp", "OutputValue": "1.2.3.4"}]
        },
    )

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
        "--region",
        "ap-northeast-1",
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

    def test_auto_derives_base_domain_and_region_from_infra_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        InfraConfig(domain="dasbd72.com", region="ap-northeast-1").save(
            frpc_cli.INFRA_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(cli.main, ["frpc", "config", "--server-addr", "1.2.3.4"])

        assert result.exit_code == 0, result.output
        config = FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE)
        assert config.base_domain == "dasbd72.com"
        assert config.region == "ap-northeast-1"

    def test_auto_derives_server_addr_via_live_lookup(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        InfraConfig(domain="dasbd72.com", region="ap-northeast-1").save(
            frpc_cli.INFRA_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(cli.main, ["frpc", "config"])

        assert result.exit_code == 0, result.output
        config = FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE)
        assert config.server_addr == "1.2.3.4"

    def test_auto_derives_token_via_live_lookup(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code == 0, result.output
        config = FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE)
        assert config.token == "fake-token"

    def test_explicit_flags_override_infra_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        InfraConfig(domain="dasbd72.com", region="ap-northeast-1").save(
            frpc_cli.INFRA_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--server-addr",
                "9.9.9.9",
                "--base-domain",
                "other.com",
                "--region",
                "us-east-1",
                "--token",
                "explicit-token",
            ],
        )

        assert result.exit_code == 0, result.output
        config = FrpcConfig.load(frpc_cli.FRPC_CONFIG_FILE)
        assert config.server_addr == "9.9.9.9"
        assert config.base_domain == "other.com"
        assert config.region == "us-east-1"
        assert config.token == "explicit-token"

    def test_errors_when_region_unresolvable(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--server-addr",
                "1.2.3.4",
                "--base-domain",
                "dasbd72.com",
            ],
        )

        assert result.exit_code != 0
        assert "--region" in result.output
        assert "infra config" in result.output

    def test_errors_when_base_domain_unresolvable(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--server-addr",
                "1.2.3.4",
                "--region",
                "ap-northeast-1",
            ],
        )

        assert result.exit_code != 0
        assert "--base-domain" in result.output
        assert "infra config" in result.output

    def test_errors_when_server_addr_unresolvable(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(frpc_cli, "describe_stack", _stack_not_found)
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--base-domain",
                "dasbd72.com",
                "--region",
                "ap-northeast-1",
            ],
        )

        assert result.exit_code != 0
        assert "--server-addr" in result.output
        assert "infra deploy" in result.output

    def test_errors_when_token_unresolvable(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(frpc_cli.token_module, "fetch_token", _param_not_found)
        runner = CliRunner()

        result = runner.invoke(cli.main, _config_args())

        assert result.exit_code != 0
        assert "--token" in result.output
        assert "infra deploy" in result.output

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

    def test_reraises_unexpected_lookup_errors_clearly(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def broken_lookup(_region: str) -> dict:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "DescribeStacks"
            )

        monkeypatch.setattr(frpc_cli, "describe_stack", broken_lookup)
        runner = CliRunner()

        result = runner.invoke(
            cli.main,
            [
                "frpc",
                "config",
                "--base-domain",
                "dasbd72.com",
                "--region",
                "ap-northeast-1",
            ],
        )

        assert result.exit_code != 0
        assert "AccessDenied" in result.output or "nope" in result.output

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
                "--region",
                "ap-northeast-1",
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
                "--region",
                "ap-northeast-1",
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
                "--region",
                "ap-northeast-1",
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
                "--region",
                "ap-northeast-1",
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

        runner.invoke(
            cli.main,
            [
                "frpc",
                "render",
                "--server-addr",
                "1.2.3.4",
                "--base-domain",
                "dasbd72.com",
                "--region",
                "ap-northeast-1",
                "--token",
                "explicit-token",
            ],
        )

        assert not frpc_cli.FRPC_CONFIG_FILE.exists()
        assert not frpc_cli.SERVICES_FILE.exists()
        assert not frpc_cli.FRPC_TOML_FILE.exists()
        assert not frpc_cli.SYSTEMD_UNIT_FILE.exists()
        assert fake_collaborators["subprocess_calls"] == []

    def test_auto_derives_from_infra_config_and_live_lookups(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        InfraConfig(domain="dasbd72.com", region="ap-northeast-1").save(
            frpc_cli.INFRA_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(cli.main, ["frpc", "render"])

        assert result.exit_code == 0, result.output
        assert 'serverAddr = "1.2.3.4"' in result.output
        assert 'auth.token = "fake-token"' in result.output

    def test_renders_proxy_block_from_service_flag(
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
                "--region",
                "ap-northeast-1",
                "--token",
                "tok",
                "--service",
                "openwebui=192.168.50.10:8080",
            ],
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
            [
                "frpc",
                "render",
                "--server-addr",
                "1.2.3.4",
                "--base-domain",
                "dasbd72.com",
                "--region",
                "ap-northeast-1",
                "--token",
                "tok",
                "--service",
                "wireguard=127.0.0.1:51820/udp:51820",
            ],
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
            [
                "frpc",
                "render",
                "--server-addr",
                "1.2.3.4",
                "--base-domain",
                "dasbd72.com",
                "--region",
                "ap-northeast-1",
                "--token",
                "tok",
                "--service",
                "wireguard=127.0.0.1:51820/udp:not-a-port",
            ],
        )

        assert result.exit_code != 0
        assert "--service" in result.output

    def test_rejects_malformed_service_flag(
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
                "--region",
                "ap-northeast-1",
                "--token",
                "tok",
                "--service",
                "openwebui-no-target",
            ],
        )

        assert result.exit_code != 0
        assert "--service" in result.output

    def test_errors_when_token_unresolvable(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(frpc_cli.token_module, "fetch_token", _param_not_found)
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
                "--region",
                "ap-northeast-1",
            ],
        )

        assert result.exit_code != 0
        assert "--token" in result.output


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
    assert "aws-reverse-tunnel frpc config" in result.output


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


def test_add_does_not_call_ssm(
    isolated_paths: Path, fake_collaborators: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    runner.invoke(cli.main, _config_args())
    monkeypatch.setattr(frpc_cli.token_module, "fetch_token", _param_not_found)

    result = runner.invoke(cli.main, ["frpc", "add", "openwebui", "192.168.50.10:8080"])

    assert result.exit_code == 0, result.output


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
