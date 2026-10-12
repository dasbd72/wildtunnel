from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from wildtunnel.providers.aws_lightsail import cli as aws_lightsail_cli
from wildtunnel.providers.aws_lightsail.config import AwsLightsailConfig


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        aws_lightsail_cli, "AWS_LIGHTSAIL_CONFIG_FILE", tmp_path / "config.json"
    )
    monkeypatch.setattr(aws_lightsail_cli, "FRP_TOKEN_FILE", tmp_path / "frp-token")
    monkeypatch.setattr(
        aws_lightsail_cli, "FRP_STATS_PASSWORD_FILE", tmp_path / "frp-stats-password"
    )
    monkeypatch.setattr(
        aws_lightsail_cli,
        "DASHBOARD_ADMIN_PASSWORD_FILE",
        tmp_path / "dashboard-admin-password",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def fake_default_region(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        region_name = "ap-northeast-1"

    monkeypatch.setattr(aws_lightsail_cli.boto3, "Session", FakeSession)


def _seed_config(path: Path, **overrides: object) -> None:
    defaults: dict[str, object] = {"domain": "example.com", "region": "ap-northeast-1"}
    defaults.update(overrides)
    AwsLightsailConfig(**defaults).save(path)  # type: ignore[arg-type]


def _saved_config(path: Path) -> AwsLightsailConfig:
    return AwsLightsailConfig.load(path)


class TestConfig:
    def test_saves_with_explicit_flags(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["config", "--domain", "example.com", "--region", "us-east-1"],
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        assert config == AwsLightsailConfig(domain="example.com", region="us-east-1")

    def test_defaults_region_from_boto3_session(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail, ["config", "--domain", "example.com"]
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        assert config.region == "ap-northeast-1"

    def test_errors_clearly_when_no_region_available(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class NoRegionSession:
            region_name = None

        monkeypatch.setattr(aws_lightsail_cli.boto3, "Session", NoRegionSession)
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail, ["config", "--domain", "example.com"]
        )

        assert result.exit_code != 0
        assert "--region" in result.output

    def test_rejects_malformed_extra_port_range(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            [
                "config",
                "--domain",
                "example.com",
                "--extra-port-range",
                "not-a-range",
            ],
        )

        assert result.exit_code != 0
        assert "--extra-port-range" in result.output

    def test_prompts_before_overwriting_changed_values(
        self, isolated_paths: Path
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE, domain="old.com")
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["config", "--domain", "new.com", "--region", "ap-northeast-1"],
            input="n\n",
        )

        assert result.exit_code != 0
        assert _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE).domain == (
            "old.com"
        )

    def test_yes_flag_skips_confirmation(self, isolated_paths: Path) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE, domain="old.com")
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            [
                "config",
                "--domain",
                "new.com",
                "--region",
                "ap-northeast-1",
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output
        assert _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE).domain == (
            "new.com"
        )

    def test_overwrites_cleanly_when_switching_from_a_different_provider(
        self, isolated_paths: Path
    ) -> None:
        aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE.write_text(
            '{"provider": "aws-ec2", "domain": "old.com", '
            '"extra_port_range": null, "aws_ec2": {"region": "us-east-1"}}'
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["config", "--domain", "new.com", "--region", "ap-northeast-1"],
        )

        assert result.exit_code == 0, result.output
        assert _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE) == (
            AwsLightsailConfig(domain="new.com", region="ap-northeast-1")
        )

    def test_saves_no_tls_when_given(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            [
                "config",
                "--domain",
                "example.com",
                "--region",
                "us-east-1",
                "--no-tls",
            ],
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        assert config.no_tls is True

    def test_no_tls_defaults_to_false(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["config", "--domain", "example.com", "--region", "us-east-1"],
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        assert config.no_tls is False


class TestDeploy:
    def test_requires_prior_config(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code != 0
        assert "aws-lightsail config" in result.output

    def test_errors_clearly_when_config_is_for_a_different_provider(
        self, isolated_paths: Path
    ) -> None:
        aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE.write_text(
            '{"provider": "aws-ec2", "domain": "old.com", '
            '"extra_port_range": null, "aws_ec2": {"region": "us-east-1"}}'
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code != 0
        assert "aws-ec2" in result.output
        assert "aws-lightsail config" in result.output

    def test_deploys_and_prints_public_ip(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli,
            "deploy_instance",
            lambda _config, _token, **_kwargs: "1.2.3.4",
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code == 0, result.output
        assert "1.2.3.4" in result.output

    def test_generates_token_once_and_reuses_it(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        seen_tokens: list[str] = []

        def _fake_deploy(_config: object, token: str, **_kwargs: object) -> str:
            seen_tokens.append(token)
            return "1.2.3.4"

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _fake_deploy)
        runner = CliRunner()

        runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])
        runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert len(seen_tokens) == 2
        assert seen_tokens[0] == seen_tokens[1]

    def test_generates_stats_password_once_and_reuses_it(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        seen_passwords: list[str] = []

        def _fake_deploy(_config: object, _token: str, **kwargs: object) -> str:
            seen_passwords.append(str(kwargs["frp_stats_password"]))
            return "1.2.3.4"

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _fake_deploy)
        runner = CliRunner()

        runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])
        runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert len(seen_passwords) == 2
        assert seen_passwords[0] == seen_passwords[1]

    def test_generates_dashboard_admin_password_once_and_reuses_it(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        seen_passwords: list[str] = []

        def _fake_deploy(_config: object, _token: str, **kwargs: object) -> str:
            seen_passwords.append(str(kwargs["dashboard_admin_password"]))
            return "1.2.3.4"

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _fake_deploy)
        runner = CliRunner()

        runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])
        runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert len(seen_passwords) == 2
        assert seen_passwords[0] == seen_passwords[1]

    def test_deploy_prints_the_dashboard_admin_password(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli,
            "deploy_instance",
            lambda _config, _token, **_kwargs: "1.2.3.4",
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code == 0, result.output
        assert "admin password" in result.output.lower()
        password = aws_lightsail_cli.DASHBOARD_ADMIN_PASSWORD_FILE.read_text().strip()
        assert password in result.output

    def test_wraps_client_error_clearly(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)

        def _boom(_config: object, _token: str, **_kwargs: object) -> str:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                "CreateInstances",
            )

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _boom)
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code != 0
        assert "deploy failed" in result.output

    def test_rejects_malformed_ssh_cidr(self, isolated_paths: Path) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["deploy", "--ssh-cidr", "not-a-cidr"],
        )

        assert result.exit_code != 0
        assert "--ssh-cidr" in result.output

    def test_rejects_ipv6_ssh_cidr(self, isolated_paths: Path) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["deploy", "--ssh-cidr", "::1/128"],
        )

        assert result.exit_code != 0
        assert "--ssh-cidr" in result.output

    def test_passes_ssh_cidr_through_to_deploy_instance(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        seen_cidrs: list[object] = []

        def _fake_deploy(_config: object, _token: str, **kwargs: object) -> str:
            seen_cidrs.append(kwargs["ssh_cidr"])
            return "1.2.3.4"

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _fake_deploy)
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["deploy", "--ssh-cidr", "1.2.3.4/32"],
        )

        assert result.exit_code == 0, result.output
        assert seen_cidrs == ["1.2.3.4/32"]

    def test_defaults_ssh_cidr_to_none(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        seen_cidrs: list[object] = []

        def _fake_deploy(_config: object, _token: str, **kwargs: object) -> str:
            seen_cidrs.append(kwargs["ssh_cidr"])
            return "1.2.3.4"

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _fake_deploy)
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code == 0, result.output
        assert seen_cidrs == [None]

    def test_passes_no_tls_config_through_to_deploy_instance(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE, no_tls=True)
        seen: list[bool] = []

        def _fake_deploy(
            config: AwsLightsailConfig, _token: str, **_kwargs: object
        ) -> str:
            seen.append(config.no_tls)
            return "1.2.3.4"

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _fake_deploy)
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code == 0, result.output
        assert seen == [True]

    def test_wraps_timeout_waiting_for_running_clearly(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)

        def _boom(_config: object, _token: str, **_kwargs: object) -> str:
            raise TimeoutError("instance 'wildtunnel' did not reach running in time")

        monkeypatch.setattr(aws_lightsail_cli, "deploy_instance", _boom)
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["deploy"])

        assert result.exit_code != 0
        assert "deploy failed" in result.output


class TestDestroy:
    def test_requires_prior_config(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["destroy"])

        assert result.exit_code != 0
        assert "aws-lightsail config" in result.output

    def test_destroys(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        calls = []
        monkeypatch.setattr(
            aws_lightsail_cli, "destroy_instance", lambda region: calls.append(region)
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["destroy"])

        assert result.exit_code == 0, result.output
        assert calls == ["ap-northeast-1"]


class TestStatus:
    def test_reports_not_deployed(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli, "describe_instance", lambda _region: None
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["status"])

        assert result.exit_code == 0, result.output
        assert "aws-lightsail deploy" in result.output

    def test_reports_live_instance_state(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli,
            "describe_instance",
            lambda _region: {
                "state": {"name": "running"},
                "publicIpAddress": "1.2.3.4",
                "isStaticIp": True,
            },
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["status"])

        assert result.exit_code == 0, result.output
        assert "running" in result.output
        assert "1.2.3.4" in result.output


class TestDiff:
    def test_reports_not_deployed(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli, "describe_port_states", lambda _region: None
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["diff"])

        assert result.exit_code == 0, result.output
        assert "aws-lightsail deploy" in result.output

    def test_reports_in_sync_when_no_drift(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli,
            "describe_port_states",
            lambda _region: [
                {
                    "fromPort": 80,
                    "toPort": 80,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": 443,
                    "toPort": 443,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": 7000,
                    "toPort": 7000,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": -1,
                    "toPort": -1,
                    "protocol": "icmp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": 22,
                    "toPort": 22,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": [],
                    "cidrListAliases": ["lightsail-connect"],
                },
            ],
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["diff"])

        assert result.exit_code == 0, result.output
        assert "in sync" in result.output

    def test_reports_drift(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli, "describe_port_states", lambda _region: []
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["diff"])

        assert result.exit_code == 0, result.output
        assert "+ open tcp 80-80" in result.output

    def test_rejects_malformed_ssh_cidr(self, isolated_paths: Path) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail,
            ["diff", "--ssh-cidr", "not-a-cidr"],
        )

        assert result.exit_code != 0
        assert "--ssh-cidr" in result.output

    def test_shows_ssh_as_drift_when_ssh_cidr_given_but_not_open(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli,
            "describe_port_states",
            lambda _region: [
                {
                    "fromPort": 80,
                    "toPort": 80,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": 443,
                    "toPort": 443,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": 7000,
                    "toPort": 7000,
                    "protocol": "tcp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
                {
                    "fromPort": -1,
                    "toPort": -1,
                    "protocol": "icmp",
                    "state": "open",
                    "cidrs": ["0.0.0.0/0"],
                },
            ],
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_lightsail_cli.aws_lightsail, ["diff", "--ssh-cidr", "1.2.3.4/32"]
        )

        assert result.exit_code == 0, result.output
        assert "+ open tcp 22-22" in result.output


class TestInfo:
    def test_requires_deployed_instance(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli, "describe_instance", lambda _region: None
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["info"])

        assert result.exit_code != 0
        assert "aws-lightsail deploy" in result.output

    def test_requires_local_token_file(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        monkeypatch.setattr(
            aws_lightsail_cli,
            "describe_instance",
            lambda _region: {"publicIpAddress": "1.2.3.4"},
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["info"])

        assert result.exit_code != 0
        assert "no local frp token" in result.output

    def test_prints_server_addr_domain_and_token(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(aws_lightsail_cli.AWS_LIGHTSAIL_CONFIG_FILE)
        aws_lightsail_cli.FRP_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        aws_lightsail_cli.FRP_TOKEN_FILE.write_text("s3cret-token")
        monkeypatch.setattr(
            aws_lightsail_cli,
            "describe_instance",
            lambda _region: {"publicIpAddress": "1.2.3.4"},
        )
        runner = CliRunner()

        result = runner.invoke(aws_lightsail_cli.aws_lightsail, ["info"])

        assert result.exit_code == 0, result.output
        assert "server-addr:  1.2.3.4" in result.output
        assert "base-domain:  example.com" in result.output
        assert "token:        s3cret-token" in result.output
