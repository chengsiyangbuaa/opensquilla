"""Build-mode verification: the fixed checklist decides the task state."""

from __future__ import annotations

from pathlib import Path

from opensquilla.contrib.codetask import build_verify
from opensquilla.contrib.codetask.types import TaskState


def _make_repo(tmp_path: Path, *, pkg: bool = True, lock: bool = True) -> Path:
    if pkg:
        (tmp_path / "package.json").write_text("{}")
    if lock:
        (tmp_path / "package-lock.json").write_text("{}")
    return tmp_path


class _FakeProc:
    def __init__(self, returncode: int, out: str = "", err: str = "") -> None:
        self.returncode = returncode
        self.stdout = out
        self.stderr = err


def test_missing_lockfile_is_environment_blocked(tmp_path):
    repo = _make_repo(tmp_path, lock=False)
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.ENVIRONMENT_BLOCKED
    assert "package-lock.json" in out.detail
    assert out.build.all_passed is False
    assert out.build.checks == []


def test_all_checks_pass_is_verified(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(build_verify.subprocess, "run", lambda *a, **k: _FakeProc(0, "ok"))
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.VERIFIED
    assert out.build.all_passed is True
    assert [c.name for c in out.build.checks] == ["npm_ci", "build", "package"]
    assert all(c.ok and c.ran for c in out.build.checks)


def test_npm_ci_failure_is_environment_blocked_and_stops_early(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(build_verify.subprocess, "run", lambda *a, **k: _FakeProc(1, "", "npm err"))
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.ENVIRONMENT_BLOCKED
    # stops at the first failing check
    assert [c.name for c in out.build.checks] == ["npm_ci"]
    assert out.build.checks[0].ok is False
    assert out.build.all_passed is False


def test_build_failure_is_failed(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)

    def fake_run(argv, **k):
        if argv[:2] == ["npm", "run"]:  # the build step
            return _FakeProc(1, "", "tsc error")
        return _FakeProc(0)

    monkeypatch.setattr(build_verify.subprocess, "run", fake_run)
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.FAILED
    assert [c.name for c in out.build.checks] == ["npm_ci", "build"]
    assert out.build.checks[-1].ok is False
    assert "build" in out.detail


def test_package_failure_is_failed(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)

    def fake_run(argv, **k):
        if argv[0] == "npx":  # electron-builder
            return _FakeProc(1, "", "packaging error")
        return _FakeProc(0)

    monkeypatch.setattr(build_verify.subprocess, "run", fake_run)
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.FAILED
    assert [c.name for c in out.build.checks] == ["npm_ci", "build", "package"]
    assert out.build.checks[-1].ok is False


def test_command_not_found_is_recorded(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)

    def fake_run(*a, **k):
        raise FileNotFoundError("npm")

    monkeypatch.setattr(build_verify.subprocess, "run", fake_run)
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.ENVIRONMENT_BLOCKED  # npm_ci is the first check
    assert out.build.checks[0].ran is False
    assert "not found" in out.build.checks[0].raw_tail


def test_package_step_is_mac_dmg_on_darwin(monkeypatch):
    monkeypatch.setattr(build_verify.sys, "platform", "darwin")
    name, argv = build_verify._package_step()
    assert name == "package" and "--mac" in argv


def test_package_step_is_linux_dir_off_darwin(monkeypatch):
    monkeypatch.setattr(build_verify.sys, "platform", "linux")
    name, argv = build_verify._package_step()
    assert "--linux" in argv and "--dir" in argv


def test_find_installers_empty_off_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(build_verify.sys, "platform", "linux")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "x.dmg").write_text("y")
    assert build_verify._find_installers(tmp_path) == []


def test_find_installers_lists_all_dmgs_on_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(build_verify.sys, "platform", "darwin")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "app-arm64.dmg").write_text("y")
    (tmp_path / "dist" / "app-x64.dmg").write_text("y")
    got = build_verify._find_installers(tmp_path)
    assert len(got) == 2 and all(p.endswith(".dmg") for p in got)


def test_darwin_clean_package_without_dmg_is_failed(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(build_verify.sys, "platform", "darwin")
    monkeypatch.setattr(build_verify.subprocess, "run", lambda *a, **k: _FakeProc(0, "ok"))
    out = build_verify.verify_build(repo)  # all steps exit 0 but dist/ has no .dmg
    assert out.state is TaskState.FAILED
    assert "no .dmg" in out.detail
    assert out.build.all_passed is False


def test_darwin_package_with_dmg_is_verified(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(build_verify.sys, "platform", "darwin")
    monkeypatch.setattr(build_verify.subprocess, "run", lambda *a, **k: _FakeProc(0, "ok"))
    (repo / "dist").mkdir()
    (repo / "dist" / "app-1.0.0.dmg").write_text("y")
    out = build_verify.verify_build(repo)
    assert out.state is TaskState.VERIFIED
    assert out.build.installer_path.endswith("app-1.0.0.dmg")
    assert out.build.installer_paths == [out.build.installer_path]
