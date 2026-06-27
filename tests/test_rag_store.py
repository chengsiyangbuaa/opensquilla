from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from opensquilla.gateway.config import RagSourceConfig
from opensquilla.rag.store import RagStore
from opensquilla.rag.types import (
    ParsedDocument,
    RagIndexJobStatus,
    RagIndexSummary,
    RagSearchOpts,
    RagShowSelector,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _store(tmp_path: Path) -> RagStore:
    store = RagStore(tmp_path / "rag.db")
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_rag_store_initialize_creates_schema(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        health = await store.health()
        with sqlite3.connect(tmp_path / "rag.db") as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
                )
            }
            schema_version = conn.execute(
                "SELECT value FROM rag_meta WHERE key = 'rag_schema_version'"
            ).fetchone()[0]

        assert health["healthy"] is True
        assert health["collections"] == 0
        assert health["documents"] == 0
        assert health["chunks"] == 0
        assert schema_version == "1"
        assert {
            "rag_meta",
            "rag_collections",
            "rag_documents",
            "rag_chunks",
            "rag_chunks_fts",
            "rag_embedding_cache",
            "rag_index_jobs",
        }.issubset(tables)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rag_store_syncs_collections_from_config(tmp_path: Path) -> None:
    source_root = tmp_path / "docs"
    store = await _store(tmp_path)
    try:
        await store.sync_collections(
            [
                RagSourceConfig(
                    id="project-docs",
                    path=str(source_root),
                    include=["**/*.md"],
                    exclude=["**/.git/**"],
                    watch=True,
                    enabled=False,
                    scale_hint="medium",
                )
            ]
        )

        collections = await store.list_collections()

        assert collections == [
            {
                "id": "project-docs",
                "name": "project-docs",
                "root_path": str(source_root.resolve()),
                "include": ["**/*.md"],
                "exclude": ["**/.git/**"],
                "scale_hint": "medium",
                "enabled": False,
                "watch": True,
                "created_at": collections[0]["created_at"],
                "updated_at": collections[0]["updated_at"],
            }
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rag_store_upsert_search_show_and_remove_document(tmp_path: Path) -> None:
    content = "\n".join(
        [
            "# RAG Design",
            "OpenSquilla local document RAG stores evidence chunks.",
            "The schema keeps documents separate from memory.",
        ]
    )
    store = await _store(tmp_path)
    try:
        chunks = await store.upsert_document_with_chunks(
            collection_id="project-docs",
            source_path=str(tmp_path / "docs" / "rag.md"),
            rel_path="guides/rag.md",
            content=content,
            parsed=ParsedDocument(
                title="RAG Design",
                text=content,
                mime="text/markdown",
                metadata={"parser": "markdown", "lang": "en"},
            ),
            file_hash=_hash(content),
            size_bytes=len(content.encode("utf-8")),
            mtime=123.0,
            chunk_tokens=400,
            chunk_overlap=50,
        )

        results = await store.search(
            "evidence chunks",
            RagSearchOpts(max_results=3, min_score=0.0, collection_id="project-docs"),
        )
        shown = await store.show(RagShowSelector(path="guides/rag.md"))
        document_id = results[0].document_id
        assert await store.document_hash(
            "project-docs",
            str(tmp_path / "docs" / "rag.md"),
        ) == _hash(content)

        assert chunks == 1
        assert results
        assert results[0].path == "guides/rag.md"
        assert results[0].title == "RAG Design"
        assert results[0].mime == "text/markdown"
        assert results[0].citation == "guides/rag.md#L1-L3"
        assert "evidence chunks" in results[0].text
        assert shown["found"] is True
        assert shown["document"]["parser_status"] == "indexed"
        assert shown["document"]["metadata"]["lang"] == "en"
        assert shown["chunks"][0]["id"] == results[0].chunk_id

        await store.remove_document(document_id)
        assert await store.search("evidence chunks", RagSearchOpts(min_score=0.0)) == []
        assert await store.show(RagShowSelector(document_id=document_id)) == {"found": False}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rag_store_replaces_document_chunks_on_reupsert(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    source_path = str(tmp_path / "docs" / "rag.md")
    try:
        await store.upsert_document_with_chunks(
            collection_id="project-docs",
            source_path=source_path,
            rel_path="rag.md",
            content="old alpha content",
            parsed=ParsedDocument(
                title=None,
                text="old alpha content",
                mime="text/plain",
                metadata={"parser": "text"},
            ),
            file_hash=_hash("old alpha content"),
            size_bytes=17,
            mtime=1.0,
            chunk_tokens=400,
            chunk_overlap=50,
        )
        await store.upsert_document_with_chunks(
            collection_id="project-docs",
            source_path=source_path,
            rel_path="rag.md",
            content="new beta content",
            parsed=ParsedDocument(
                title=None,
                text="new beta content",
                mime="text/plain",
                metadata={"parser": "text"},
            ),
            file_hash=_hash("new beta content"),
            size_bytes=16,
            mtime=2.0,
            chunk_tokens=400,
            chunk_overlap=50,
        )

        old_results = await store.search("alpha", RagSearchOpts(min_score=0.0))
        new_results = await store.search("beta", RagSearchOpts(min_score=0.0))

        assert old_results == []
        assert len(new_results) == 1
        assert new_results[0].text == "new beta content"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rag_store_marks_failed_document_without_searchable_chunks(
    tmp_path: Path,
) -> None:
    store = await _store(tmp_path)
    try:
        await store.mark_document_failed(
            collection_id="docs",
            source_path=str(tmp_path / "bad.md"),
            rel_path="bad.md",
            file_hash="bad-hash",
            size_bytes=12,
            mtime=1.0,
            parser="markdown",
            error="decode failed",
            mime="text/markdown",
            metadata={"reason": "decode"},
        )

        results = await store.search("decode", RagSearchOpts(min_score=0.0))
        shown = await store.show(RagShowSelector(path="bad.md"))

        assert results == []
        assert shown["found"] is True
        assert shown["document"]["parser_status"] == "failed"
        assert shown["document"]["parser_error"] == "decode failed"
        assert shown["document"]["metadata"]["reason"] == "decode"
        assert shown["chunks"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rag_store_show_chunk_and_line_slice(tmp_path: Path) -> None:
    content = "line one\nline two target\nline three"
    store = await _store(tmp_path)
    try:
        await store.upsert_document_with_chunks(
            collection_id="docs",
            source_path=str(tmp_path / "doc.txt"),
            rel_path="doc.txt",
            content=content,
            parsed=ParsedDocument(
                title="doc",
                text=content,
                mime="text/plain",
                metadata={"parser": "text"},
            ),
            file_hash=_hash(content),
            size_bytes=len(content),
            mtime=1.0,
            chunk_tokens=400,
            chunk_overlap=50,
        )
        result = (await store.search("target", RagSearchOpts(min_score=0.0)))[0]

        shown = await store.show(
            RagShowSelector(chunk_id=result.chunk_id, from_line=2, lines=1)
        )

        assert shown["found"] is True
        assert shown["kind"] == "chunk"
        assert shown["chunk"]["text"] == "line two target"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rag_store_records_index_job_success_and_failure(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    try:
        completed_job = await store.begin_index_job("docs")
        await store.finish_index_job(
            completed_job,
            RagIndexSummary(
                job_id=completed_job,
                collection_id="docs",
                indexed_documents=2,
                skipped_documents=1,
                failed_documents=0,
                removed_documents=1,
                capped=True,
                errors=["size cap reached"],
            ),
        )
        failed_job = await store.begin_index_job(None)
        await store.fail_index_job(failed_job, "boom")

        with sqlite3.connect(tmp_path / "rag.db") as conn:
            rows = conn.execute(
                """SELECT id, status, indexed_documents, skipped_documents,
                          failed_documents, error, progress_json
                   FROM rag_index_jobs
                   ORDER BY started_at"""
            ).fetchall()

        assert rows[0][0] == completed_job
        assert rows[0][1] == RagIndexJobStatus.completed.value
        assert rows[0][2:5] == (2, 1, 0)
        assert json.loads(rows[0][6]) == {
            "removed_documents": 1,
            "capped": True,
            "errors": ["size cap reached"],
        }
        assert rows[1][0] == failed_job
        assert rows[1][1] == RagIndexJobStatus.failed.value
        assert rows[1][5] == "boom"
    finally:
        await store.close()
