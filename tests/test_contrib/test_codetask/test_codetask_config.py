"""Unit tests for opensquilla.contrib.codetask.config."""

import sys

from opensquilla.contrib.codetask import config


class TestSlugify:
    def test_basic(self):
        assert config.slugify("Fix CSV export bug") == "fix-csv-export-bug"

    def test_strips_punctuation_and_unicode(self):
        assert config.slugify("修复 CSV 导出 bug!!!") == "csv-bug"

    def test_truncates(self):
        s = config.slugify("a" * 100, max_len=10)
        assert len(s) <= 10

    def test_empty_fallback(self):
        assert config.slugify("!!!") == "task"


class TestPaths:
    def test_runs_root_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENSQUILLA_CODETASK_RUNS_DIR", str(tmp_path))
        assert config.runs_root() == tmp_path
        assert config.run_dir("r1") == tmp_path / "r1"
        assert config.repo_dir("r1") == tmp_path / "r1" / "repo"
        assert config.scratch_dir("r1") == tmp_path / "r1" / "scratch"
        assert config.artifact_path("r1", "result.json") == tmp_path / "r1" / "result.json"

    def test_agent_python_default(self, monkeypatch):
        monkeypatch.delenv("OPENSQUILLA_CODETASK_AGENT_PYTHON", raising=False)
        assert config.agent_python() == sys.executable

    def test_agent_python_override(self, monkeypatch):
        monkeypatch.setenv("OPENSQUILLA_CODETASK_AGENT_PYTHON", "/opt/py/bin/python")
        assert config.agent_python() == "/opt/py/bin/python"

    def test_prompt_template_shipped(self, monkeypatch):
        monkeypatch.delenv("OPENSQUILLA_CODETASK_PROMPT_TEMPLATE", raising=False)
        path = config.prompt_template_path()
        assert path.exists()
        body = path.read_text()
        for slot in ("{task}", "{env_hints}", "{scratch_dir}", "{manifest_name}"):
            assert slot in body
