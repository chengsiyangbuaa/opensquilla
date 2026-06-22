"""Build-mode verification for code-task (app / from-scratch generation).

Red->green->regression fits "fix a bug in an existing repo". Generating/editing
an app has no such test loop, so build mode instead runs a FIXED, runner-owned
checklist that proves the app actually builds: install from the committed
lockfile, build, and PACKAGE for the host platform.

The package step is host-aware:
- macOS  -> `electron-builder --mac`, which validates packaging AND produces the
  deliverable installer (a .dmg). Signing auto-discovery is disabled so an
  unsigned .dmg is built deterministically with no keychain/identity prompt.
- other  -> `electron-builder --linux --dir`, which validates the packaging
  chain without an installer (a macOS .dmg can only be built on macOS).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from opensquilla.contrib.codetask.types import BuildCheck, BuildResult, TaskState

_TAIL_LINES = 25

# Build unsigned, deterministically: never auto-discover a keychain identity
# (which can prompt/hang or sign host-dependently in an automated run).
_PACKAGE_ENV = {"CSC_IDENTITY_AUTO_DISCOVERY": "false"}


def _package_step() -> tuple[str, list[str]]:
    """Host-platform electron packaging command (name, argv)."""
    if sys.platform == "darwin":
        # Produces the .dmg installer (the deliverable) and validates packaging.
        return "package", ["npx", "electron-builder", "--mac", "--publish", "never"]
    # Linux/other: validate the packaging chain without producing an installer.
    return "package", ["npx", "electron-builder", "--linux", "--dir", "--publish", "never"]


def _checklist() -> list[tuple[str, list[str]]]:
    # `npm ci` (NOT install) installs strictly from the committed lockfile and
    # never mutates it, so build verification leaves the collected change clean.
    return [
        ("npm_ci", ["npm", "ci"]),
        ("build", ["npm", "run", "build"]),
        _package_step(),
    ]


def _find_installers(repo: Path) -> list[str]:
    """Produced installer artifacts (the .dmg files) on macOS — the deliverables.

    electron-builder's output directory is configurable (``directories.output``,
    default ``dist``, but a generated app may set ``release/`` or another name),
    so search the whole repo tree for ``*.dmg`` instead of only ``dist/`` — else
    a real, successful build whose installer landed elsewhere is misreported as
    "produced no .dmg". ``node_modules`` and ``.git`` are pruned (no build output
    lives there and walking them is slow). Multi-arch/universal builds can emit
    more than one .dmg, so return all. Empty off macOS (the Linux/CI package step
    intentionally builds no installer).
    """
    if sys.platform != "darwin":
        return []
    skip = {"node_modules", ".git"}
    found: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        found.extend(os.path.join(root, f) for f in files if f.endswith(".dmg"))
    return sorted(found)


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

    env = {**os.environ, **_PACKAGE_ENV}
    checklist = _checklist()
    checks: list[BuildCheck] = []
    for name, argv in checklist:
        chk = BuildCheck(name=name, command=" ".join(argv))
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=check_timeout,
                env=env,
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

    all_passed = len(checks) == len(checklist) and all(c.ok for c in checks)
    build = BuildResult(checks=checks, all_passed=all_passed)

    if all_passed:
        # On macOS the package step must yield the .dmg deliverable; a clean exit
        # with no .dmg (e.g. config emits only a zip/dir) is NOT a real success.
        if sys.platform == "darwin":
            installers = _find_installers(repo)
            if not installers:
                build.all_passed = False
                return BuildVerificationOutcome(
                    state=TaskState.FAILED,
                    build=build,
                    detail="packaging exited cleanly but produced no .dmg installer",
                )
            build.installer_paths = installers
            build.installer_path = installers[0]
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
