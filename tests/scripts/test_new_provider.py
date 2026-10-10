import subprocess
import sys
from pathlib import Path

import pytest

from scripts.new_provider import generate_provider, main


def test_generates_expected_file_set_for_fresh_name(tmp_path: Path) -> None:
    generate_provider("aws_lightsail", tmp_path)

    src_dir = tmp_path / "src" / "wildtunnel" / "providers" / "aws_lightsail"
    tests_dir = tmp_path / "tests" / "providers" / "aws_lightsail"
    assert (src_dir / "__init__.py").exists()
    assert (src_dir / "cli.py").exists()
    assert (src_dir / "config.py").exists()
    assert (tests_dir / "__init__.py").exists()
    assert (tests_dir / "test_cli.py").exists()
    assert (tests_dir / "test_config.py").exists()


def test_refuses_to_overwrite_existing_provider(tmp_path: Path) -> None:
    generate_provider("aws_lightsail", tmp_path)
    cli_path = (
        tmp_path / "src" / "wildtunnel" / "providers" / "aws_lightsail" / "cli.py"
    )
    cli_path.write_text("# in-progress work, do not clobber\n")

    with pytest.raises(FileExistsError):
        generate_provider("aws_lightsail", tmp_path)

    assert cli_path.read_text() == "# in-progress work, do not clobber\n"


def test_generated_python_files_are_syntactically_valid(tmp_path: Path) -> None:
    generate_provider("aws_lightsail", tmp_path)

    src_dir = tmp_path / "src" / "wildtunnel" / "providers" / "aws_lightsail"
    tests_dir = tmp_path / "tests" / "providers" / "aws_lightsail"
    for path in (
        src_dir / "__init__.py",
        src_dir / "cli.py",
        src_dir / "config.py",
        tests_dir / "__init__.py",
        tests_dir / "test_cli.py",
        tests_dir / "test_config.py",
    ):
        compile(path.read_text(), str(path), "exec")


def test_generated_cli_defines_hyphenated_click_group_with_command_stubs(
    tmp_path: Path,
) -> None:
    generate_provider("aws_lightsail", tmp_path)

    cli_src = (
        tmp_path / "src" / "wildtunnel" / "providers" / "aws_lightsail" / "cli.py"
    ).read_text()

    assert '@click.group(name="aws-lightsail")' in cli_src
    for command in ("config", "deploy", "destroy", "diff", "status", "info"):
        assert f"def {command}(" in cli_src
    assert cli_src.count('"not implemented yet"') == 6


def test_generated_config_documents_nested_schema_and_stubs_load_save(
    tmp_path: Path,
) -> None:
    generate_provider("aws_lightsail", tmp_path)

    config_src = (
        tmp_path / "src" / "wildtunnel" / "providers" / "aws_lightsail" / "config.py"
    ).read_text()

    assert "class AwsLightsailConfig" in config_src
    assert "def load(" in config_src
    assert "def save(" in config_src
    assert "NotImplementedError" in config_src
    assert "providers/aws_ec2/config.py" in config_src


def test_generated_test_stubs_collect_and_skip_under_isolated_pytest(
    tmp_path: Path,
) -> None:
    generate_provider("aws_lightsail", tmp_path)
    tests_dir = tmp_path / "tests" / "providers" / "aws_lightsail"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir), "-q"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 skipped" in result.stdout


@pytest.mark.parametrize(
    "bad_name", ["Aws-Lightsail", "aws lightsail", "2aws", "aws-ec2"]
)
def test_rejects_name_that_is_not_a_lowercase_identifier(
    tmp_path: Path, bad_name: str
) -> None:
    with pytest.raises(ValueError):
        generate_provider(bad_name, tmp_path)

    assert not (tmp_path / "src").exists()


def test_main_generates_provider_and_prints_manual_followup_checklist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["aws_lightsail"], repo_root=tmp_path)

    assert exit_code == 0
    assert (
        tmp_path / "src" / "wildtunnel" / "providers" / "aws_lightsail" / "cli.py"
    ).exists()
    out = capsys.readouterr().out
    assert "cli.py" in out
    assert "pyproject.toml" in out
    assert "README" in out


def test_main_reports_error_and_exits_nonzero_for_invalid_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["Not-A-Valid-Name"], repo_root=tmp_path)

    assert exit_code != 0
    assert not (tmp_path / "src").exists()
    assert "invalid provider name" in capsys.readouterr().err


def test_main_reports_error_and_exits_nonzero_when_already_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["aws_lightsail"], repo_root=tmp_path)
    capsys.readouterr()

    exit_code = main(["aws_lightsail"], repo_root=tmp_path)

    assert exit_code != 0
    assert "already exists" in capsys.readouterr().err
