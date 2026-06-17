"""Build-mode verification for code-task (app / from-scratch generation).

Red->green->regression fits "fix a bug in an existing repo". Generating an app
from scratch has no such test loop, so build mode instead runs a FIXED,
runner-owned checklist that proves the generated app actually builds: install
from the committed lockfile, build, and package (Linux --dir, no GUI launch).
The checklist is identical across apps and the agent cannot substitute its own
"passing" check (codex review: the runner must own the build commands).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from opensquilla.contrib.codetask.types import BuildCheck, BuildResult, TaskState

# (name, argv). `npm ci` (NOT `npm install`) installs strictly from the
# committed package-lock.json and never mutates it, so build verification
# leaves the collected change untouched.
CHECKLIST: list[tuple[str, list[str]]] = [
    ("npm_ci", ["npm", "ci"]),
    ("build", ["npm", "run", "build"]),
    ("package", ["npx", "electron-builder", "--linux", "--dir", "--publish", "never"]),
]

_TAIL_LINES = 25


@dataclass
class BuildVerificationOutcome:
    state: TaskState
    build: BuildResult
    detail: str = ""


def _tail(text: str, n: int = _TAIL_LINES) -> str:
    return "\n".join((text or "").splitlines()[-n:])


def verify_build(
    repo: Path,
    *,
    check_timeout: int = 1800,
) -> BuildVerificationOutcome:
    """Run the fixed build checklist from the repo root and decide the state."""
    missing = [
        name
        for name in ("package.json", "package-lock.json")
        if not (repo / name).is_file()
    ]
    if missing:
        return BuildVerificationOutcome(
            state=TaskState.ENVIRONMENT_BLOCKED,
            build=BuildResult(checks=[], all_passed=False),
            detail=(
                f"missing {', '.join(missing)} — the app must be scaffolded and "
                "`npm install` run so a lockfile exists in the change"
            ),
        )

    checks: list[BuildCheck] = []
    for name, argv in CHECKLIST:
        chk = BuildCheck(name=name, command=" ".join(argv))
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=check_timeout,
            )
            chk.ran = True
            chk.exit_code = proc.returncode
            chk.ok = proc.returncode == 0
            chk.raw_tail = _tail((proc.stdout or "") + (proc.stderr or ""))
        except subprocess.TimeoutExpired:
            chk.ran = True
            chk.ok = False
            chk.raw_tail = f"TIMEOUT after {check_timeout}s"
        except FileNotFoundError as exc:
            chk.ran = False
            chk.ok = False
            chk.raw_tail = f"command not found: {exc}"
        chk.duration_seconds = round(time.monotonic() - start, 1)
        checks.append(chk)
        if not chk.ok:
            break  # later checks are meaningless once one fails

    all_passed = len(checks) == len(CHECKLIST) and all(c.ok for c in checks)
    build = BuildResult(checks=checks, all_passed=all_passed)
    if all_passed:
        return BuildVerificationOutcome(state=TaskState.VERIFIED, build=build)

    failed = next((c for c in checks if not c.ok), None)
    # Deps failing to install = the environment is the blocker; build/package
    # failing = the generated app does not build.
    state = (
        TaskState.ENVIRONMENT_BLOCKED
        if failed is not None and failed.name == "npm_ci"
        else TaskState.FAILED
    )
    detail = (
        f"build check failed: {failed.name}"
        if failed is not None
        else "build verification did not complete"
    )
    return BuildVerificationOutcome(state=state, build=build, detail=detail)
