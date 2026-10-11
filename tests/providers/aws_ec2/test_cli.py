import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from wildtunnel.providers.aws_ec2 import cli as aws_ec2_cli
from wildtunnel.providers.aws_ec2.config import AwsEc2Config


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(aws_ec2_cli, "AWS_EC2_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(aws_ec2_cli, "AWS_EC2_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        aws_ec2_cli,
        "DASHBOARD_ADMIN_PASSWORD_FILE",
        tmp_path / "dashboard-admin-password",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def fake_collaborators(monkeypatch: pytest.MonkeyPatch) -> dict:
    recorded: dict = {"subprocess_calls": []}

    monkeypatch.setattr(aws_ec2_cli.shutil, "which", lambda _name: "/usr/bin/cdk")
    monkeypatch.setattr(aws_ec2_cli.importlib, "import_module", lambda _name: object())

    class FakeSession:
        region_name = "ap-northeast-1"

    monkeypatch.setattr(aws_ec2_cli.boto3, "Session", FakeSession)

    def fake_run(args, **kwargs):
        recorded["subprocess_calls"].append({"args": args, "cwd": kwargs.get("cwd")})

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(aws_ec2_cli.subprocess, "run", fake_run)
    return recorded


def _saved_config(path: Path) -> AwsEc2Config:
    return AwsEc2Config.load(path)


class TestConfig:
    def test_saves_with_explicit_flags(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "example.com", "--region", "us-east-1"],
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        assert config == AwsEc2Config(domain="example.com", region="us-east-1")

    def test_defaults_region_from_boto3_session(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2, ["config", "--domain", "example.com"]
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        assert config.region == "ap-northeast-1"

    def test_errors_when_no_region_available(
        self, isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class NoRegionSession:
            region_name = None

        monkeypatch.setattr(aws_ec2_cli.boto3, "Session", NoRegionSession)
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2, ["config", "--domain", "example.com"]
        )

        assert result.exit_code != 0
        assert "--region" in result.output
        assert not aws_ec2_cli.AWS_EC2_CONFIG_FILE.exists()

    def test_errors_cleanly_on_malformed_config_file(
        self, isolated_paths: Path
    ) -> None:
        # A config file written before this schema existed (no "aws_ec2" key)
        # must fail with a clean message, not a raw KeyError traceback.
        aws_ec2_cli.AWS_EC2_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        aws_ec2_cli.AWS_EC2_CONFIG_FILE.write_text(
            '{"domain": "example.com", "region": "us-east-1"}'
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "aws-ec2 config" in result.output

    def test_prompts_before_overwriting_changed_config(
        self, isolated_paths: Path
    ) -> None:
        AwsEc2Config(domain="old.com", region="us-east-1").save(
            aws_ec2_cli.AWS_EC2_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "new.com", "--region", "us-east-1"],
            input="n\n",
        )

        assert result.exit_code != 0
        assert _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE).domain == "old.com"

    def test_overwrites_when_confirmed(self, isolated_paths: Path) -> None:
        AwsEc2Config(domain="old.com", region="us-east-1").save(
            aws_ec2_cli.AWS_EC2_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "new.com", "--region", "us-east-1"],
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE).domain == "new.com"

    def test_yes_flag_skips_confirmation(self, isolated_paths: Path) -> None:
        AwsEc2Config(domain="old.com", region="us-east-1").save(
            aws_ec2_cli.AWS_EC2_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "new.com", "--region", "us-east-1", "--yes"],
        )

        assert result.exit_code == 0, result.output
        assert _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE).domain == "new.com"

    def test_overwrites_cleanly_when_switching_from_a_different_provider(
        self, isolated_paths: Path
    ) -> None:
        aws_ec2_cli.AWS_EC2_CONFIG_FILE.write_text(
            '{"provider": "aws-lightsail", "domain": "old.com", '
            '"extra_port_range": null, "aws_lightsail": {"region": "us-east-1"}}'
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "new.com", "--region", "ap-northeast-1"],
        )

        assert result.exit_code == 0, result.output
        assert _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE) == AwsEc2Config(
            domain="new.com", region="ap-northeast-1"
        )

    def test_saves_extra_port_range_when_given(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            [
                "config",
                "--domain",
                "example.com",
                "--region",
                "us-east-1",
                "--extra-port-range",
                "20000-20100",
            ],
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        assert config.extra_port_range == (20000, 20100)

    def test_extra_port_range_defaults_to_unset(self, isolated_paths: Path) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "example.com", "--region", "us-east-1"],
        )

        assert result.exit_code == 0, result.output
        config = _saved_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        assert config.extra_port_range is None

    @pytest.mark.parametrize(
        "bad_range", ["garbage", "20100-20000", "0-100", "100-99999"]
    )
    def test_rejects_malformed_extra_port_range(
        self, isolated_paths: Path, bad_range: str
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            [
                "config",
                "--domain",
                "example.com",
                "--region",
                "us-east-1",
                "--extra-port-range",
                bad_range,
            ],
        )

        assert result.exit_code != 0
        assert "--extra-port-range" in result.output

    def test_no_prompt_when_values_unchanged(self, isolated_paths: Path) -> None:
        AwsEc2Config(domain="example.com", region="us-east-1").save(
            aws_ec2_cli.AWS_EC2_CONFIG_FILE
        )
        runner = CliRunner()

        result = runner.invoke(
            aws_ec2_cli.aws_ec2,
            ["config", "--domain", "example.com", "--region", "us-east-1"],
        )

        assert result.exit_code == 0, result.output


def _seed_config(path: Path) -> AwsEc2Config:
    config = AwsEc2Config(domain="example.com", region="ap-northeast-1")
    config.save(path)
    return config


class TestDeployDestroyDiff:
    def test_deploy_requires_prior_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code != 0
        assert "aws-ec2 config" in result.output
        assert fake_collaborators["subprocess_calls"] == []

    def test_deploy_errors_clearly_when_config_is_for_a_different_provider(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        aws_ec2_cli.AWS_EC2_CONFIG_FILE.write_text(
            '{"provider": "aws-lightsail", "domain": "old.com", '
            '"extra_port_range": null, "aws_lightsail": {"region": "us-east-1"}}'
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code != 0
        assert "aws-lightsail" in result.output
        assert "aws-ec2 config" in result.output

    def test_deploy_uses_saved_config_with_no_flags(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code == 0, result.output
        call = fake_collaborators["subprocess_calls"][0]
        assert call["cwd"] == aws_ec2_cli.AWS_EC2_CACHE_DIR
        assert call["args"][0] == "/usr/bin/cdk"
        assert f"{sys.executable} -m wildtunnel.providers.aws_ec2.app" in call["args"]
        assert "domain=example.com" in call["args"]
        assert "region=ap-northeast-1" in call["args"]
        assert call["args"][-1] == "deploy"

    def test_deploy_passes_extra_port_range_context_when_configured(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        AwsEc2Config(
            domain="example.com",
            region="ap-northeast-1",
            extra_port_range=(20000, 20100),
        ).save(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code == 0, result.output
        call_args = fake_collaborators["subprocess_calls"][0]["args"]
        assert "extraPortRangeStart=20000" in call_args
        assert "extraPortRangeEnd=20100" in call_args

    def test_deploy_omits_extra_port_range_context_when_unconfigured(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code == 0, result.output
        call_args = " ".join(fake_collaborators["subprocess_calls"][0]["args"])
        assert "extraPortRange" not in call_args

    def test_deploy_prints_the_admin_password_written_by_the_cdk_subprocess(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        # The real `cdk deploy` subprocess (app.py) generates and caches
        # this file as a side effect; the fake subprocess here doesn't
        # actually run app.py, so seed the file the same way it would.
        aws_ec2_cli.DASHBOARD_ADMIN_PASSWORD_FILE.parent.mkdir(
            parents=True, exist_ok=True
        )
        aws_ec2_cli.DASHBOARD_ADMIN_PASSWORD_FILE.write_text("generated-admin-pw")
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code == 0, result.output
        assert "generated-admin-pw" in result.output

    def test_deploy_fails_cleanly_when_cdk_subprocess_fails(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)

        def failing_run(args, **kwargs):
            raise aws_ec2_cli.subprocess.CalledProcessError(1, args)

        monkeypatch.setattr(aws_ec2_cli.subprocess, "run", failing_run)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["deploy"])

        assert result.exit_code != 0
        assert isinstance(result.exception, SystemExit) or result.exception is None
        assert "cdk deploy failed" in result.output

    def test_destroy_uses_saved_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["destroy"])

        assert result.exit_code == 0, result.output
        assert fake_collaborators["subprocess_calls"][0]["args"][-1] == "destroy"

    def test_diff_uses_saved_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["diff"])

        assert result.exit_code == 0, result.output
        assert fake_collaborators["subprocess_calls"][0]["args"][-1] == "diff"


class TestStatus:
    def test_requires_prior_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["status"])

        assert result.exit_code != 0
        assert "aws-ec2 config" in result.output

    def test_prints_stack_status_and_outputs(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)

        class FakeCloudFormation:
            def describe_stacks(self, StackName: str) -> dict:
                return {
                    "Stacks": [
                        {
                            "StackStatus": "CREATE_COMPLETE",
                            "Outputs": [
                                {"OutputKey": "ElasticIp", "OutputValue": "1.2.3.4"}
                            ],
                        }
                    ]
                }

        monkeypatch.setattr(
            aws_ec2_cli.boto3,
            "client",
            lambda _service, region_name: FakeCloudFormation(),
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["status"])

        assert result.exit_code == 0, result.output
        assert "CREATE_COMPLETE" in result.output
        assert "ElasticIp: 1.2.3.4" in result.output

    def test_errors_clearly_when_stack_not_found(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)

        class NotFoundCloudFormation:
            def describe_stacks(self, StackName: str) -> dict:
                raise ClientError(
                    {"Error": {"Code": "ValidationError", "Message": "does not exist"}},
                    "DescribeStacks",
                )

        monkeypatch.setattr(
            aws_ec2_cli.boto3,
            "client",
            lambda _service, region_name: NotFoundCloudFormation(),
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["status"])

        assert result.exit_code != 0
        assert "aws-ec2 deploy" in result.output

    def test_reraises_unexpected_errors_clearly(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)

        class BrokenCloudFormation:
            def describe_stacks(self, StackName: str) -> dict:
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "nope"}},
                    "DescribeStacks",
                )

        monkeypatch.setattr(
            aws_ec2_cli.boto3,
            "client",
            lambda _service, region_name: BrokenCloudFormation(),
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["status"])

        assert result.exit_code != 0
        assert "AccessDenied" in result.output or "nope" in result.output


class TestInfo:
    def test_requires_prior_config(
        self, isolated_paths: Path, fake_collaborators: dict
    ) -> None:
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["info"])

        assert result.exit_code != 0
        assert "aws-ec2 config" in result.output

    def test_prints_frpc_values_and_command(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        monkeypatch.setattr(
            aws_ec2_cli,
            "describe_stack",
            lambda _region: {
                "Outputs": [{"OutputKey": "ElasticIp", "OutputValue": "1.2.3.4"}]
            },
        )
        monkeypatch.setattr(
            aws_ec2_cli.token_module, "fetch_token", lambda _param, _region: "s3cret"
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["info"])

        assert result.exit_code == 0, result.output
        assert "server-addr:  1.2.3.4" in result.output
        assert "base-domain:  example.com" in result.output
        assert "s3cret" in result.output
        # A copy-pasteable frpc config command.
        assert "wildtunnel frpc config" in result.output
        assert "--server-addr 1.2.3.4" in result.output
        assert "--token s3cret" in result.output

    def test_errors_when_stack_not_found(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)

        def _not_found(_region: str) -> dict:
            raise ClientError(
                {"Error": {"Code": "ValidationError", "Message": "does not exist"}},
                "DescribeStacks",
            )

        monkeypatch.setattr(aws_ec2_cli, "describe_stack", _not_found)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["info"])

        assert result.exit_code != 0
        assert "aws-ec2 deploy" in result.output

    def test_errors_when_elastic_ip_output_missing(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        monkeypatch.setattr(
            aws_ec2_cli, "describe_stack", lambda _region: {"Outputs": []}
        )
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["info"])

        assert result.exit_code != 0
        assert "ElasticIp" in result.output

    def test_errors_when_token_cannot_be_fetched(
        self,
        isolated_paths: Path,
        fake_collaborators: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_config(aws_ec2_cli.AWS_EC2_CONFIG_FILE)
        monkeypatch.setattr(
            aws_ec2_cli,
            "describe_stack",
            lambda _region: {
                "Outputs": [{"OutputKey": "ElasticIp", "OutputValue": "1.2.3.4"}]
            },
        )

        def _boom(_param: str, _region: str) -> str:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "nope"}},
                "GetParameter",
            )

        monkeypatch.setattr(aws_ec2_cli.token_module, "fetch_token", _boom)
        runner = CliRunner()

        result = runner.invoke(aws_ec2_cli.aws_ec2, ["info"])

        assert result.exit_code != 0
        assert "token" in result.output
