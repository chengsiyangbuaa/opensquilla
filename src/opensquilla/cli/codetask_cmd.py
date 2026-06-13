"""``opensquilla code-task`` — solve a real-repo coding task with an agent.

Host mode only in v1: the repo is cloned to a disposable working directory
and the agent runs as a host subprocess (no Docker). Treat the target repo
as TRUSTED — this is not an OS isolation boundary.

This module is lazy: heavy imports happen inside the command body so
``--help`` stays cheap and importing opensquilla does not pull it in.
"""

from __future__ import annotations

import json

import typer

codetask_app = typer.Typer(
    help="Solve a real-repository coding task (GitHub issue or feature request).",
    no_args_is_help=True,
)


@codetask_app.callback()
def _codetask_main() -> None:
    """Solve real-repository coding tasks with an OpenSquilla agent (host mode)."""
    # Present so the single ``solve`` command is not collapsed away by Typer
    # (keeps ``opensquilla code-task solve ...`` as an explicit subcommand).


@codetask_app.command("solve")
def solve(
    repo: str = typer.Option(..., help="Repo to work on: a git URL or a local path."),
    issue: int = typer.Option(None, "--issue", help="GitHub issue number (needs `gh`)."),
    task: str = typer.Option(None, "--task", help="Free-form task / feature request text."),
    task_file: str = typer.Option(
        None, "--task-file", help="Path to a file holding the task description."
    ),
    base: str = typer.Option(None, "--base", help="Base ref to start from (default: HEAD)."),
    shallow: bool = typer.Option(False, "--shallow", help="Shallow clone (no history)."),
    model: str = typer.Option("", help="Model override; empty lets the router/config decide."),
    thinking: str = typer.Option("", help="Thinking effort; empty lets config decide."),
    timeout: int = typer.Option(1800, help="Agent timeout in seconds."),
    run_id: str = typer.Option("", help="Run identifier (auto-generated when empty)."),
    json_output: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the trusted-host confirmation prompt."
    ),
) -> None:
    """Clone the repo, run an agent to solve the task, and verify the result."""
    from opensquilla.contrib.codetask.inputs import InputError
    from opensquilla.contrib.codetask.report import render
    from opensquilla.contrib.codetask.runner import TRUSTED_HOST_WARNING
    from opensquilla.contrib.codetask.runner import solve as run_solve
    from opensquilla.contrib.codetask.types import PRODUCTIVE_STATES, TaskState
    from opensquilla.contrib.codetask.workspace import WorkspaceError

    given = [x for x in (issue, task, task_file) if x not in (None, "")]
    if len(given) != 1:
        typer.secho(
            "Pass exactly one of --issue, --task, or --task-file.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)

    # Trusted-host gate. --json is non-interactive, so it must carry an
    # explicit --yes rather than silently skipping the gate (codex review #5).
    if not yes:
        if json_output:
            typer.secho(
                "Refusing to run non-interactively without --yes. code-task runs an "
                "agent on the host (not a sandbox); pass --yes to confirm the repo is "
                "trusted.",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(2)
        typer.secho(TRUSTED_HOST_WARNING, fg=typer.colors.YELLOW)
        if not typer.confirm(f"Run code-task against {repo}?", default=False):
            raise typer.Exit(1)

    try:
        result = run_solve(
            repo=repo,
            issue=issue,
            task=task or None,
            task_file=task_file or None,
            base_ref=base or None,
            shallow=shallow,
            model=model,
            thinking=thinking,
            timeout=timeout,
            run_id=run_id or None,
        )
    except InputError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(2) from exc
    except WorkspaceError as exc:
        typer.secho(f"workspace error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    if json_output:
        from opensquilla.contrib.codetask.runner import _result_to_dict

        typer.echo(json.dumps(_result_to_dict(result), ensure_ascii=False))
    else:
        typer.echo(render(result))

    # Exit non-zero on clearly unproductive outcomes so scripts can branch.
    if result.state in (
        TaskState.FAILED,
        TaskState.ENVIRONMENT_BLOCKED,
        TaskState.INVALID_ACCEPTANCE_TEST,
    ):
        raise typer.Exit(1)
    _ = PRODUCTIVE_STATES  # documented states for consumers
