from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from opensquilla.gateway.config import GatewayConfig
from opensquilla.rag.paths import (
    resolve_agent_rag_db,
    resolve_source_root,
    safe_relative_to_root,
)


@pytest.fixture(autouse=True)
def _clear_rag_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("OPENSQUILLA_RAG_"):
            monkeypatch.delenv(key, raising=False)


def test_rag_defaults_are_stable() -> None:
    config = GatewayConfig()

    assert config.rag.enabled is False
    assert config.rag.db_name == "rag.db"
    assert config.rag.retrieval_mode == "hybrid"
    assert config.rag.embedding.provider == "auto"
    assert config.rag.sources == []
    assert config.rag.chunk_tokens == 400
    assert config.rag.chunk_overlap == 50
    assert config.rag.max_file_size_kb == 2048
    assert config.rag.max_total_size_kb == 102400
    assert config.rag.max_files == 5000


def test_rag_valid_source_config_loads() -> None:
    config = GatewayConfig(
        rag={
            "enabled": True,
            "sources": [
                {
                    "id": "project.docs_1",
                    "path": "~/docs",
                    "include": ["**/*.md"],
                    "exclude": ["**/.git/**"],
                    "watch": True,
                    "enabled": False,
                    "scale_hint": "medium",
                }
            ],
        }
    )

    source = config.rag.sources[0]
    assert config.rag.enabled is True
    assert source.id == "project.docs_1"
    assert source.path == "~/docs"
    assert source.include == ["**/*.md"]
    assert source.exclude == ["**/.git/**"]
    assert source.watch is True
    assert source.enabled is False
    assert source.scale_hint == "medium"


@pytest.mark.parametrize("source_id", ["", "  ", "project/docs", "project docs", "docs:main"])
def test_rag_rejects_invalid_source_id(source_id: str) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig(rag={"sources": [{"id": source_id, "path": "~/docs"}]})


def test_rag_rejects_duplicate_source_ids() -> None:
    with pytest.raises(ValidationError):
        GatewayConfig(
            rag={
                "sources": [
                    {"id": "docs", "path": "~/docs-a"},
                    {"id": "docs", "path": "~/docs-b"},
                ]
            }
        )


@pytest.mark.parametrize("chunk_overlap", [400, 401])
def test_rag_rejects_overlap_greater_or_equal_chunk_tokens(chunk_overlap: int) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig(rag={"chunk_tokens": 400, "chunk_overlap": chunk_overlap})


@pytest.mark.parametrize(
    "db_name",
    ["../rag.db", "nested/rag.db", "nested\\rag.db", ".", "..", ""],
)
def test_rag_rejects_path_like_db_name(db_name: str) -> None:
    with pytest.raises(ValidationError):
        GatewayConfig(rag={"db_name": db_name})


def test_rag_loads_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "opensquilla.toml"
    config_path.write_text(
        "\n".join(
            [
                "[rag]",
                "enabled = true",
                'retrieval_mode = "fts_only"',
                'db_name = "project-rag.db"',
                "chunk_tokens = 500",
                "chunk_overlap = 80",
                "max_file_size_kb = 4096",
                "",
                "[rag.embedding]",
                'provider = "none"',
                "",
                "[[rag.sources]]",
                'id = "project-docs"',
                'path = "~/Documents/project-docs"',
                'include = ["**/*.md"]',
                'exclude = ["**/.git/**"]',
                "watch = false",
                "enabled = true",
                'scale_hint = "small"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.load_from_toml(config_path)

    assert config.rag.enabled is True
    assert config.rag.retrieval_mode == "fts_only"
    assert config.rag.db_name == "project-rag.db"
    assert config.rag.embedding.provider == "none"
    assert config.rag.chunk_tokens == 500
    assert config.rag.chunk_overlap == 80
    assert config.rag.max_file_size_kb == 4096
    assert config.rag.sources[0].id == "project-docs"


def test_rag_env_overrides_scalar_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSQUILLA_RAG_ENABLED", "true")
    monkeypatch.setenv("OPENSQUILLA_RAG_DB_NAME", "env-rag.db")
    monkeypatch.setenv("OPENSQUILLA_RAG_RETRIEVAL_MODE", "fts_only")
    monkeypatch.setenv("OPENSQUILLA_RAG_EMBEDDING__PROVIDER", "none")

    config = GatewayConfig()

    assert config.rag.enabled is True
    assert config.rag.db_name == "env-rag.db"
    assert config.rag.retrieval_mode == "fts_only"
    assert config.rag.embedding.provider == "none"


def test_rag_paths_resolve_agent_db_without_creating(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    path = resolve_agent_rag_db("main", state_dir, "custom-rag.db")

    assert path == state_dir / "agents" / "main" / "custom-rag.db"
    assert not path.parent.exists()


def test_rag_resolve_source_root_expands_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_source_root("~/docs") == (tmp_path / "docs").resolve()


def test_rag_safe_relative_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "docs" / "a.md"

    assert safe_relative_to_root(root, inside) == str(Path("docs") / "a.md")

    with pytest.raises(ValueError):
        safe_relative_to_root(root, tmp_path / "outside.md")
