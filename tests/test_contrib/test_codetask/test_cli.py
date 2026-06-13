"""Unit tests for the `opensquilla code-task` CLI subcommand."""

import subprocess
import sys

from typer.testing import CliRunner

from opensquilla.cli.codetask_cmd import codetask_app

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(codetask_app, ["--help"])
    assert result.exit_code == 0
    assert "solve" in result.output


def test_solve_help_lists_inputs():
    result = runner.invoke(codetask_app, ["solve", "--help"])
    assert result.exit_code == 0
    for opt in ("--repo", "--issue", "--task", "--task-file"):
        assert opt in result.output


def test_solve_requires_one_task_input():
    # repo given, but no task input → exit 2.
    result = runner.invoke(codetask_app, ["solve", "--repo", "/tmp/x", "--yes"])
    assert result.exit_code == 2
    assert "exactly one" in result.output


def test_solve_rejects_two_task_inputs():
    result = runner.invoke(
        codetask_app,
        ["solve", "--repo", "/tmp/x", "--issue", "1", "--task", "y", "--yes"],
    )
    assert result.exit_code == 2


def test_cli_main_does_not_import_codetask():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "import opensquilla.cli.main\n"
            "assert 'opensquilla.contrib.codetask' not in sys.modules, "
            "'CLI registration must stay lazy'\n",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
