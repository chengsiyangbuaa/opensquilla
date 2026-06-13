"""Host working tree: clone the target repo and prepare a task branch.

v1 is host-only and always clones fresh into a disposable run directory.
``--in-place`` is intentionally out of scope (codex review #5): mutating a
user's real checkout needs worktree/LFS/submodule handling deferred to v2.

SECURITY: this is NOT an OS isolation boundary. Running an agent that may
install dependencies and execute repo code on the host is trusted-host
execution. Only point code-task at repositories you trust.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from opensquilla.contrib.codetask.config import (
    GIT_USER_EMAIL,
    GIT_USER_NAME,
    TASK_BRANCH_PREFIX,
    repo_dir,
    run_dir,
)

logger = logging.getLogger(__name__)

CLONE_TIMEOUT = 1800  # seconds; large repos


class WorkspaceError(RuntimeError):
    """Clone/branch/setup failed."""


@dataclass
class PreparedRepo:
    path: Path
    base_ref: str
    base_commit: str
    branch: str


def _git(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def is_dirty(path: Path) -> bool:
    """True if the given checkout has uncommitted changes."""
    r = _git(["status", "--porcelain"], path)
    return r.returncode == 0 and bool(r.stdout.strip())


def prepare_repo(
    run_id: str,
    repo: str,
    *,
    base_ref: str | None = None,
    shallow: bool = False,
    slug: str = "task",
) -> PreparedRepo:
    """Clone ``repo`` (URL or local path) into the run dir and branch off.

    Local-path sources are cloned too (never mutated in place), so a failed
    or messy run can be discarded by deleting the run dir.
    """
    dest = repo_dir(run_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)

    clone_cmd = ["git", "clone"]
    if shallow:
        clone_cmd += ["--depth", "1"]
    clone_cmd += [_normalize_source(repo), str(dest)]
    try:
        result = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT)
    except FileNotFoundError as exc:
        raise WorkspaceError("git is not installed.") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git clone timed out after {CLONE_TIMEOUT}s.") from exc
    if result.returncode != 0:
        raise WorkspaceError(f"git clone failed: {(result.stderr or '').strip()[-400:]}")

    # Check out the requested base.
    if base_ref:
        r = _git(["checkout", base_ref], dest)
        if r.returncode != 0:
            raise WorkspaceError(f"git checkout {base_ref} failed: {r.stderr.strip()}")

    head = _git(["rev-parse", "HEAD"], dest)
    base_commit = head.stdout.strip() if head.returncode == 0 else ""
    resolved_ref = base_ref or _current_branch(dest) or base_commit

    # Local commit identity (does not touch the user's global git config).
    _git(["config", "user.email", GIT_USER_EMAIL], dest)
    _git(["config", "user.name", GIT_USER_NAME], dest)

    branch = f"{TASK_BRANCH_PREFIX}{slug}"
    r = _git(["checkout", "-b", branch], dest)
    if r.returncode != 0:
        # Branch name collision: fall back to a run-suffixed name.
        branch = f"{TASK_BRANCH_PREFIX}{slug}-{run_id.split('-')[-1]}"
        r = _git(["checkout", "-b", branch], dest)
        if r.returncode != 0:
            raise WorkspaceError(f"could not create task branch: {r.stderr.strip()}")

    logger.info("Prepared %s @ %s on branch %s", repo, base_commit[:12], branch)
    return PreparedRepo(path=dest, base_ref=resolved_ref, base_commit=base_commit, branch=branch)


def _normalize_source(repo: str) -> str:
    """Expand a local path source; pass URLs through untouched."""
    if "://" in repo or repo.startswith("git@"):
        return repo
    p = Path(repo).expanduser()
    if p.exists():
        return str(p.resolve())
    return repo


def _current_branch(path: Path) -> str:
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    return r.stdout.strip() if r.returncode == 0 else ""


def collect_change(repo: Path, base_commit: str) -> tuple[int, str, str]:
    """Return (files_changed, diffstat, full_patch) of work vs base_commit.

    The agent commits on the task branch; uncommitted work is also captured
    so a non-committing agent still yields a diff.
    """
    _git(["add", "-A"], repo)
    diffstat = _git(["diff", "--stat", base_commit], repo).stdout.strip()
    patch = _git(["diff", "--no-color", base_commit], repo).stdout
    names = _git(["diff", "--name-only", base_commit], repo).stdout.strip()
    files_changed = len([n for n in names.splitlines() if n.strip()])
    return files_changed, diffstat, patch


def count_commits(repo: Path, base_commit: str) -> int:
    r = _git(["rev-list", "--count", f"{base_commit}..HEAD"], repo)
    try:
        return int(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def cleanup(run_id: str, keep: bool = True) -> None:
    """Remove the cloned repo (artifacts under run dir are kept by default)."""
    if keep:
        return
    target = run_dir(run_id)
    if target.exists():
        shutil.rmtree(target)
