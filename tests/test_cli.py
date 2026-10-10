from click.testing import CliRunner

from aws_reverse_tunnel import cli


def test_main_wires_up_aws_ec2_and_frpc_subgroups() -> None:
    runner = CliRunner()

    result = runner.invoke(cli.main, ["--help"])

    assert result.exit_code == 0, result.output
    assert "aws-ec2" in result.output
    assert "frpc" in result.output


def test_old_top_level_commands_are_gone() -> None:
    runner = CliRunner()

    for command in ["config", "add", "del", "list", "status"]:
        result = runner.invoke(cli.main, [command, "--help"])
        assert result.exit_code != 0
