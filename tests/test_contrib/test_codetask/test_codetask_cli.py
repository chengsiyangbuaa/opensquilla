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


def test_json_without_yes_is_refused():
    # --json is non-interactive; the trusted-host gate must not be skipped
    # silently (codex review #5). It must require an explicit --yes.
    result = runner.invoke(codetask_app, ["solve", "--repo", "/tmp/x", "--task", "do it", "--json"])
    assert result.exit_code == 2
    assert "--yes" in result.output


def test_json_stdout_is_clean_with_marker_on_stderr(monkeypatch):
    # --json must keep stdout pure JSON; the run-dir startup marker goes to
    # stderr so a consumer reading stdout alone still gets parseable JSON.
    import json as _json

    import opensquilla.contrib.codetask.runner as ct_runner
    from opensquilla.contrib.codetask.types import TaskResult, TaskState

    fake = TaskResult(
        task_slug="x",
        run_id="codetask-task-fake",
        state=TaskState.VERIFIED,
        repo="/tmp/x",
        base_ref="main",
        branch="task/x",
        source="inline",
        artifact_dir="/tmp/x",
    )
    fake.verified = True
    monkeypatch.setattr(ct_runner, "solve", lambda **kw: fake)

    result = runner.invoke(
        codetask_app,
        ["solve", "--repo", "/tmp/x", "--task", "do it", "--json", "--yes"],
    )
    assert result.exit_code == 0, result.output
    # stdout parses as JSON on its own — the marker did not leak into it.
    parsed = _json.loads(result.stdout)
    assert parsed["state"] == "verified"
    assert "[code-task]" not in result.stdout
    # the run-dir announcement is on stderr.
    assert "[code-task] run started" in result.stderr


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
