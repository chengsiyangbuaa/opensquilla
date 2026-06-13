"""Unit tests for opensquilla.contrib.codetask.adapter (subprocess mocked)."""

from opensquilla.contrib.codetask import adapter
from opensquilla.contrib.codetask.adapter import LocalAdapter


class FakeResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_argv_has_host_containment_flags(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        # The agent CLI argv is embedded in the -c python code.
        captured["code"] = cmd[2]
        return FakeResult(stdout='{"status": "ok", "text": "done", "usage": {}}')

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()
    a = LocalAdapter(model="m", timeout=10)
    out = a.run("fix it", repo=repo, scratch_dir=tmp_path / "s", artifact_dir=tmp_path / "art")

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


def test_api_key_never_in_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-xyz")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeResult(stdout='{"status": "ok", "text": "done"}')

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()
    LocalAdapter().run("x", repo=repo, scratch_dir=tmp_path / "s", artifact_dir=tmp_path / "a")
    # Host mode inherits env; the key must not be embedded in the command.
    assert all("sk-or-secret-xyz" not in part for part in captured["cmd"])


def test_timeout_maps_to_environment_blocked_finish(monkeypatch, tmp_path):
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raise sp.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    repo = tmp_path / "repo"
    repo.mkdir()
    out = LocalAdapter(timeout=1).run(
        "x", repo=repo, scratch_dir=tmp_path / "s", artifact_dir=tmp_path / "a"
    )
    assert out.timeout is True
    assert out.finish_reason == "timeout"
