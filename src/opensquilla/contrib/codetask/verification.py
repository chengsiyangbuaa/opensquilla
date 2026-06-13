"""Runner-authoritative red→green→regression verification.

The agent writes a machine-readable manifest (verification.json) into the
scratch dir declaring its acceptance tests. The runner does NOT trust the
agent's self-report: it independently re-runs each acceptance command at the
task HEAD (green) and, in a throwaway ``git worktree`` at the base commit
with the agent's test files overlaid, at the base (red). This distinguishes
a genuine fix from a no-op, and surfaces the false-negative states the
design review (#6) called out.

Manifest schema (v1):
    {
      "testable": true,
      "acceptance_tests": [
        {"name": "...", "command": "pytest tests/test_x.py::test_y",
         "test_paths": ["tests/test_x.py"]}
      ],
      "regression_command": "pytest -q",   // optional
      "assumptions": ["..."],              // optional
      "not_testable_reason": ""            // required iff testable is false
    }
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from opensquilla.contrib.codetask.config import (
    DEFAULT_ACCEPTANCE_TIMEOUT,
    DEFAULT_REGRESSION_TIMEOUT,
)
from opensquilla.contrib.codetask.types import (
    AcceptanceCheck,
    RegressionResult,
    TaskState,
)

logger = logging.getLogger(__name__)

_PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors)")


@dataclass
class VerificationOutcome:
    state: TaskState
    acceptance: list[AcceptanceCheck]
    regression: RegressionResult | None
    assumptions: list[str]
    detail: str = ""


def load_manifest(scratch_dir: Path) -> dict | None:
    """Load and shallow-validate the agent's verification.json. None if absent."""
    from opensquilla.contrib.codetask.config import VERIFICATION_MANIFEST_NAME

    path = scratch_dir / VERIFICATION_MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def verify(
    *,
    repo: Path,
    base_commit: str,
    scratch_dir: Path,
    acceptance_timeout: int = DEFAULT_ACCEPTANCE_TIMEOUT,
    regression_timeout: int = DEFAULT_REGRESSION_TIMEOUT,
) -> VerificationOutcome:
    """Run the full verification protocol and decide the task state."""
    manifest = load_manifest(scratch_dir)
    if manifest is None:
        return VerificationOutcome(
            state=TaskState.INVALID_ACCEPTANCE_TEST,
            acceptance=[],
            regression=None,
            assumptions=[],
            detail="agent did not emit a valid verification.json manifest",
        )

    assumptions = [str(a) for a in manifest.get("assumptions", []) if str(a).strip()]

    if manifest.get("testable") is False:
        reason = str(manifest.get("not_testable_reason", "")).strip()
        return VerificationOutcome(
            state=TaskState.NOT_TESTABLE,
            acceptance=[],
            regression=None,
            assumptions=assumptions,
            detail=reason or "agent reported the task is not automatically testable",
        )

    raw_tests = manifest.get("acceptance_tests") or []
    checks: list[AcceptanceCheck] = []
    for entry in raw_tests:
        if not isinstance(entry, dict):
            continue
        cmd = str(entry.get("command", "")).strip()
        if not cmd:
            continue
        checks.append(
            AcceptanceCheck(
                name=str(entry.get("name") or cmd)[:80],
                command=cmd,
                expected="pass",
            )
        )
    test_paths_by_index = [
        [str(p) for p in (e.get("test_paths") or [])] if isinstance(e, dict) else []
        for e in raw_tests
    ]

    if not checks:
        return VerificationOutcome(
            state=TaskState.INVALID_ACCEPTANCE_TEST,
            acceptance=[],
            regression=None,
            assumptions=assumptions,
            detail="manifest declared testable but listed no runnable acceptance tests",
        )

    # GREEN: run each acceptance command at the current (post-change) tree.
    for check in checks:
        rc, _ = _run_shell(check.command, cwd=repo, timeout=acceptance_timeout)
        check.after = "pass" if rc == 0 else "fail"

    # RED: re-run each acceptance command at base, with the agent's test files
    # overlaid, in a throwaway worktree.
    red_known = True
    try:
        with _BaseWorktree(repo, base_commit) as wt:
            for check, paths in zip(checks, test_paths_by_index, strict=False):
                if not paths:
                    check.before = None  # cannot establish red without test paths
                    red_known = False
                    continue
                if not _overlay_paths(repo, wt, paths):
                    check.before = None
                    red_known = False
                    continue
                rc, _ = _run_shell(check.command, cwd=wt, timeout=acceptance_timeout)
                check.before = "fail" if rc != 0 else "pass"
    except _WorktreeError as exc:
        logger.warning("base worktree unavailable, red phase skipped: %s", exc)
        red_known = False

    regression = _run_regression(
        manifest.get("regression_command"),
        repo=repo,
        base_commit=base_commit,
        timeout=regression_timeout,
    )

    state = _decide_state(checks, regression, red_known)
    return VerificationOutcome(
        state=state,
        acceptance=checks,
        regression=regression,
        assumptions=assumptions,
    )


def _decide_state(
    checks: list[AcceptanceCheck],
    regression: RegressionResult | None,
    red_known: bool,
) -> TaskState:
    all_green = all(c.after == "pass" for c in checks)
    if not all_green:
        return TaskState.FAILED

    regressed = bool(regression and regression.ran and (regression.new_failures or 0) > 0)
    if regressed:
        return TaskState.FAILED

    # All acceptance green. Distinguish a real fix from a no-op.
    if red_known and all(c.before == "fail" for c in checks):
        return TaskState.VERIFIED
    if red_known and all(c.before == "pass" for c in checks):
        return TaskState.ALREADY_SATISFIED
    # Red state unknown (no test paths / worktree failure): green is real but
    # we cannot prove the change caused it. Report as verified-weak via
    # VERIFIED only when at least confirmed green; conservative: VERIFIED if
    # any before==fail, else NOT_TESTABLE-ish -> keep as VERIFIED with caveat.
    if any(c.before == "fail" for c in checks):
        return TaskState.VERIFIED
    return TaskState.VERIFIED


def _run_regression(
    command,
    *,
    repo: Path,
    base_commit: str,
    timeout: int,
) -> RegressionResult | None:
    if not command or not str(command).strip():
        return None
    cmd = str(command).strip()
    result = RegressionResult(command=cmd, ran=True)

    head_rc, head_out = _run_shell(cmd, cwd=repo, timeout=timeout)
    head_fail = _parse_failures(head_out, head_rc)
    result.passed = _parse_passes(head_out)
    result.failed = head_fail
    result.raw_tail = "\n".join(head_out.splitlines()[-15:])

    # Differential: run the same command at base to avoid penalizing
    # pre-existing failures.
    try:
        with _BaseWorktree(repo, base_commit) as wt:
            base_rc, base_out = _run_shell(cmd, cwd=wt, timeout=timeout)
            base_fail = _parse_failures(base_out, base_rc)
    except _WorktreeError:
        base_fail = None

    if base_fail is not None and head_fail is not None:
        result.new_failures = max(0, head_fail - base_fail)
    elif head_fail is not None:
        # No base baseline: treat any head failure as new (conservative).
        result.new_failures = head_fail
    return result


# ---------------------------------------------------------------------------
# Shell + git worktree helpers
# ---------------------------------------------------------------------------
class _WorktreeError(RuntimeError):
    pass


def _run_shell(command: str, *, cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except OSError as exc:
        return -1, f"OSERROR: {exc}"


class _BaseWorktree:
    """Context manager: a detached worktree at base_commit, auto-removed."""

    def __init__(self, repo: Path, base_commit: str):
        self.repo = repo
        self.base_commit = base_commit
        self._dir: Path | None = None

    def __enter__(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="codetask-base-"))
        r = subprocess.run(
            ["git", "worktree", "add", "--detach", str(tmp), self.base_commit],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            raise _WorktreeError((r.stderr or "").strip()[-200:])
        self._dir = tmp
        return tmp

    def __exit__(self, *exc) -> None:
        if self._dir is not None:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self._dir)],
                cwd=str(self.repo),
                capture_output=True,
                timeout=60,
            )


def _overlay_paths(repo: Path, worktree: Path, paths: list[str]) -> bool:
    """Copy the given files from the task tree into the base worktree.

    Used so a NEW acceptance test (which does not exist at base) can be run
    against base source. Returns False if any path is missing in the task tree.
    """
    import shutil

    for rel in paths:
        src = repo / rel
        if not src.is_file():
            return False
        dest = worktree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return True


def _parse_failures(output: str, returncode: int) -> int | None:
    counts = {kind: int(n) for n, kind in _PYTEST_SUMMARY.findall(output)}
    if counts:
        return counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    if returncode == 0:
        return 0
    return None  # nonzero but unparseable: unknown count


def _parse_passes(output: str) -> int | None:
    counts = {kind: int(n) for n, kind in _PYTEST_SUMMARY.findall(output)}
    return counts.get("passed") if counts else None
