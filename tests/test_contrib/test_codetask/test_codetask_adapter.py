"""Unit tests for opensquilla.contrib.codetask.adapter (subprocess mocked)."""

import subprocess as sp

from opensquilla.contrib.codetask import adapter
from opensquilla.contrib.codetask.adapter import LocalAdapter


class FakePopen:
    """Minimal Popen stand-in for the adapter's communicate()-based flow."""

    def __init__(self, cmd, stdout="", returncode=0, stderr="", timeout=False, **kwargs):
        self.args = cmd
        self.pid = 4242
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout = timeout
        self._calls = 0
        self.killed = False

    def communicate(self, timeout=None):
        self._calls += 1
        # Time out only on the first call (the bounded wait); the post-kill
        # drain call returns normally.
        if self._timeout and self._calls == 1:
            raise sp.TimeoutExpired(self.args, timeout or 1)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


def _install_popen(monkeypatch, captured, **popen_kw):
    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["new_session"] = kwargs.get("start_new_session")
        captured["code"] = cmd[2]
        return FakePopen(cmd, **popen_kw)

    monkeypatch.setattr(adapter.subprocess, "Popen", fake_popen)


def test_argv_has_host_containment_flags(monkeypatch, tmp_path):
    captured = {}
    _install_popen(monkeypatch, captured, stdout='{"status": "ok", "text": "done", "usage": {}}')
    repo = tmp_path / "repo"
    repo.mkdir()
    out = LocalAdapter(model="m", timeout=10).run(
        "fix it", repo=repo, scratch_dir=tmp_path / "s", artifact_dir=tmp_path / "art"
    )
    assert out.success is True
    code = captured["code"]
    for flag in (
        "--workspace",
        "--workspace-strict",
        "--workspace-lockdown",
        "--scratch-dir",
        "--stateless",
        "--permissions",
    ):
        assert flag in code
    assert captured["cwd"] == str(repo)
    # Descendant-killable process group (codex review #6).
    assert captured["new_session"] is True


def test_api_key_never_in_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-xyz")
    captured = {}
    _install_popen(monkeypatch, captured, stdout='{"status": "ok", "text": "done"}')
    repo = tmp_path / "repo"
    repo.mkdir()
    LocalAdapter().run("x", repo=repo, scratch_dir=tmp_path / "s", artifact_dir=tmp_path / "a")
    assert all("sk-or-secret-xyz" not in part for part in captured["cmd"])


def test_timeout_kills_group_and_reports_timeout(monkeypatch, tmp_path):
    captured = {}
    _install_popen(monkeypatch, captured, timeout=True)
    killed = {"group": False}
    monkeypatch.setattr(
        adapter, "_kill_process_group", lambda proc: killed.__setitem__("group", True)
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    out = LocalAdapter(timeout=1).run(
        "x", repo=repo, scratch_dir=tmp_path / "s", artifact_dir=tmp_path / "a"
    )
    assert out.timeout is True
    assert out.finish_reason == "timeout"
    assert killed["group"] is True


def test_run_clears_stale_scratch_manifest(monkeypatch, tmp_path):
    # A leftover verification.json from a prior run reusing this run_id must
    # be wiped before the agent runs, so the runner cannot read a stale
    # manifest (codex review).
    captured = {}
    _install_popen(monkeypatch, captured, stdout='{"status": "ok", "text": "done"}')
    repo = tmp_path / "repo"
    repo.mkdir()
    scratch = tmp_path / "s"
    scratch.mkdir()
    stale = scratch / "verification.json"
    stale.write_text('{"testable": true, "stale": true}')

    LocalAdapter().run("x", repo=repo, scratch_dir=scratch, artifact_dir=tmp_path / "a")

    # The stale manifest is gone (scratch was recreated clean).
    assert not stale.exists()
    assert scratch.is_dir()
