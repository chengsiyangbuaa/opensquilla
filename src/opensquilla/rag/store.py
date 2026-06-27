"""SQLite-backed store for local document RAG."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Generator, Iterable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from opensquilla.memory.embedding import _estimate_tokens, chunk_hash, chunk_text
from opensquilla.rag.schema import (
    DDL_CHUNKS,
    DDL_COLLECTIONS,
    DDL_DOCUMENTS,
    DDL_EMBEDDING_CACHE,
    DDL_FTS,
    DDL_INDEX_JOBS,
    DDL_INDEXES,
    DDL_META,
    RAG_META_SCHEMA_KEY,
    RAG_SCHEMA_VERSION,
)
from opensquilla.rag.types import (
    ParsedDocument,
    RagDocumentStatus,
    RagIndexJobStatus,
    RagIndexSummary,
    RagSearchOpts,
    RagSearchResult,
    RagShowSelector,
)

if TYPE_CHECKING:
    from opensquilla.gateway.config import RagSourceConfig
    from opensquilla.memory.embedding import EmbeddingProvider

logger = structlog.get_logger(__name__)


class _SyncCursor(AbstractAsyncContextManager["_SyncCursor"]):
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    async def fetchone(self) -> Any:
        return self._cursor.fetchone()

    async def fetchall(self) -> list[Any]:
        return list(self._cursor.fetchall())

    async def close(self) -> None:
        self._cursor.close()

    async def __aenter__(self) -> _SyncCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


class _CursorProxy(Awaitable[_SyncCursor], AbstractAsyncContextManager[_SyncCursor]):
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = _SyncCursor(cursor)

    async def _get(self) -> _SyncCursor:
        return self._cursor

    def __await__(self) -> Generator[Any, None, _SyncCursor]:
        return self._get().__await__()

    async def __aenter__(self) -> _SyncCursor:
        return self._cursor

    async def __aexit__(self, *_: object) -> None:
        await self._cursor.close()


class _SyncConnection:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> _CursorProxy:
        return _CursorProxy(self._conn.execute(sql, tuple(params)))

    async def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    async def commit(self) -> None:
        self._conn.commit()

    async def rollback(self) -> None:
        self._conn.rollback()

    async def close(self) -> None:
        self._conn.close()


def _connect_sqlite(db_path: str) -> _SyncConnection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return _SyncConnection(conn)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _stable_id(*parts: object) -> str:
    raw = "\0".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def _file_name_or_memory_path(path: str | Path) -> str | Path:
    raw = str(path)
    return raw if raw == ":memory:" else Path(raw).expanduser()


def _build_fts_query(query: str) -> str | None:
    tokens = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]{2,}", query)
    if not tokens:
        tokens = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+", query)
    if not tokens:
        return None
    phrases = re.findall(r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+", query)
    quoted = [f'"{phrase}"' for phrase in phrases] + [f'"{token}"' for token in tokens]
    return " OR ".join(dict.fromkeys(quoted))


def _bm25_to_score(rank: float) -> float:
    if rank < 0:
        relevance = -rank
        return relevance / (1.0 + relevance)
    return 1.0 / (1.0 + rank)


def _citation(path: str, start_line: int | None, end_line: int | None) -> str:
    if start_line is None or end_line is None:
        return path
    return f"{path}#L{start_line}-L{end_line}"


def _slice_lines(text: str, from_line: int | None, lines: int | None) -> str:
    if from_line is None and lines is None:
        return text
    split = text.splitlines()
    start = max(0, (from_line or 1) - 1)
    end = None if lines is None else start + max(0, lines)
    return "\n".join(split[start:end])


class RagStore:
    """Persistent local document RAG store backed by SQLite and FTS5."""

    def __init__(
        self,
        db_path: str | Path,
        embedding_provider: EmbeddingProvider | None = None,
        query_embedding_cache_mode: str = "on",
    ) -> None:
        self._db_path = _file_name_or_memory_path(db_path)
        self._provider = embedding_provider
        self._query_embedding_cache_mode = query_embedding_cache_mode
        self._db: _SyncConnection | None = None
        self._fts_available = False

    async def initialize(self) -> None:
        """Open the database and ensure the PR 2 schema exists."""

        if isinstance(self._db_path, Path):
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = _connect_sqlite(str(self._db_path))
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._ensure_schema()
        self._fts_available = True

    async def _ensure_schema(self) -> None:
        assert self._db is not None
        await self._db.execute(DDL_META)
        await self._db.execute(DDL_COLLECTIONS)
        await self._db.execute(DDL_DOCUMENTS)
        await self._db.execute(DDL_CHUNKS)
        await self._db.execute(DDL_EMBEDDING_CACHE)
        await self._db.execute(DDL_INDEX_JOBS)
        await self._db.execute(DDL_FTS)
        await self._db.executescript(DDL_INDEXES)
        await self._db.execute(
            """INSERT OR REPLACE INTO rag_meta (key, value, schema_version)
               VALUES (?, ?, ?)""",
            (RAG_META_SCHEMA_KEY, str(RAG_SCHEMA_VERSION), RAG_SCHEMA_VERSION),
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def health(self) -> dict[str, Any]:
        if self._db is None:
            return {
                "backend": "sqlite",
                "initialized": False,
                "healthy": False,
                "fts_available": False,
            }
        try:
            await self._db.execute("SELECT 1")
            counts = await self._counts()
        except Exception as exc:  # noqa: BLE001
            return {
                "backend": "sqlite",
                "initialized": True,
                "healthy": False,
                "fts_available": self._fts_available,
                "error": str(exc),
            }
        return {
            "backend": "sqlite",
            "initialized": True,
            "healthy": True,
            "fts_available": self._fts_available,
            **counts,
        }

    async def _counts(self) -> dict[str, int]:
        assert self._db is not None
        result: dict[str, int] = {}
        for key, table in (
            ("collections", "rag_collections"),
            ("documents", "rag_documents"),
            ("chunks", "rag_chunks"),
            ("jobs", "rag_index_jobs"),
        ):
            async with self._db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                row = await cur.fetchone()
            result[key] = int(row[0] if row else 0)
        return result

    async def sync_collections(self, sources: list[RagSourceConfig]) -> None:
        """Upsert configured RAG sources into collection rows."""

        assert self._db is not None
        now = time.time()
        for source in sources:
            root_path = str(Path(source.path).expanduser().resolve())
            async with self._db.execute(
                "SELECT created_at FROM rag_collections WHERE id = ?",
                (source.id,),
            ) as cur:
                row = await cur.fetchone()
            created_at = float(row[0]) if row else now
            await self._db.execute(
                """INSERT OR REPLACE INTO rag_collections
                   (id, name, root_path, include_json, exclude_json, scale_hint,
                    enabled, watch, created_at, updated_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source.id,
                    source.id,
                    root_path,
                    _json_dumps(source.include),
                    _json_dumps(source.exclude),
                    source.scale_hint,
                    1 if source.enabled else 0,
                    1 if source.watch else 0,
                    created_at,
                    now,
                    RAG_SCHEMA_VERSION,
                ),
            )
        await self._db.commit()

    async def list_collections(self) -> list[dict[str, Any]]:
        assert self._db is not None
        async with self._db.execute(
            """SELECT id, name, root_path, include_json, exclude_json, scale_hint,
                      enabled, watch, created_at, updated_at
               FROM rag_collections
               ORDER BY id"""
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "root_path": row[2],
                "include": json.loads(row[3]),
                "exclude": json.loads(row[4]),
                "scale_hint": row[5],
                "enabled": bool(row[6]),
                "watch": bool(row[7]),
                "created_at": row[8],
                "updated_at": row[9],
            }
            for row in rows
        ]

    async def begin_index_job(self, collection_id: str | None) -> str:
        assert self._db is not None
        job_id = uuid.uuid4().hex
        await self._db.execute(
            """INSERT INTO rag_index_jobs
               (id, collection_id, status, started_at, progress_json, schema_version)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                collection_id,
                RagIndexJobStatus.running.value,
                time.time(),
                "{}",
                RAG_SCHEMA_VERSION,
            ),
        )
        await self._db.commit()
        return job_id

    async def finish_index_job(self, job_id: str, summary: RagIndexSummary) -> None:
        assert self._db is not None
        progress = {
            "removed_documents": summary.removed_documents,
            "capped": summary.capped,
            "errors": summary.errors,
        }
        await self._db.execute(
            """UPDATE rag_index_jobs
               SET status = ?, finished_at = ?, indexed_documents = ?,
                   skipped_documents = ?, failed_documents = ?,
                   progress_json = ?
               WHERE id = ?""",
            (
                RagIndexJobStatus.completed.value,
                time.time(),
                summary.indexed_documents,
                summary.skipped_documents,
                summary.failed_documents,
                _json_dumps(progress),
                job_id,
            ),
        )
        await self._db.commit()

    async def fail_index_job(self, job_id: str, error: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """UPDATE rag_index_jobs
               SET status = ?, finished_at = ?, error = ?
               WHERE id = ?""",
            (RagIndexJobStatus.failed.value, time.time(), error, job_id),
        )
        await self._db.commit()

    async def document_hash(self, collection_id: str, source_path: str) -> str | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT hash FROM rag_documents WHERE collection_id = ? AND source_path = ?",
            (collection_id, source_path),
        ) as cur:
            row = await cur.fetchone()
        return str(row[0]) if row else None

    async def upsert_document_with_chunks(
        self,
        *,
        collection_id: str,
        source_path: str,
        rel_path: str,
        content: str,
        parsed: ParsedDocument,
        file_hash: str,
        size_bytes: int,
        mtime: float,
        chunk_tokens: int,
        chunk_overlap: int,
    ) -> int:
        assert self._db is not None
        document_text = parsed.text if parsed.text is not None else content
        document_id = _stable_id("document", collection_id, source_path)
        parser = str(parsed.metadata.get("parser") or "unknown")
        now = time.time()
        chunks = chunk_text(document_text, chunk_tokens, chunk_overlap)
        chunk_records: list[tuple[str, int, str, str, int, int, int]] = []
        for index, (start_line, end_line, text) in enumerate(chunks):
            text_hash = chunk_hash(text)
            chunk_id = _stable_id("chunk", document_id, index, text_hash)
            token_count = _estimate_tokens(text)
            chunk_records.append(
                (chunk_id, index, text, text_hash, token_count, start_line, end_line)
            )

        await self._db.execute("BEGIN IMMEDIATE")
        try:
            await self._db.execute(
                "DELETE FROM rag_chunks_fts WHERE document_id = ?",
                (document_id,),
            )
            await self._db.execute(
                "DELETE FROM rag_chunks WHERE document_id = ?",
                (document_id,),
            )
            await self._db.execute(
                """INSERT OR REPLACE INTO rag_documents
                   (id, collection_id, source_path, rel_path, mime, title, hash,
                    size_bytes, mtime, parser, parser_status, parser_error,
                    metadata_json, updated_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id,
                    collection_id,
                    source_path,
                    rel_path,
                    parsed.mime,
                    parsed.title,
                    file_hash,
                    size_bytes,
                    mtime,
                    parser,
                    RagDocumentStatus.indexed.value,
                    None,
                    _json_dumps(parsed.metadata),
                    now,
                    RAG_SCHEMA_VERSION,
                ),
            )
            for (
                chunk_id,
                index,
                text,
                text_hash,
                token_count,
                start_line,
                end_line,
            ) in chunk_records:
                await self._db.execute(
                    """INSERT INTO rag_chunks
                       (id, document_id, collection_id, chunk_index, text, hash,
                        token_count, start_line, end_line, page, section,
                        metadata_json, updated_at, schema_version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk_id,
                        document_id,
                        collection_id,
                        index,
                        text,
                        text_hash,
                        token_count,
                        start_line,
                        end_line,
                        None,
                        None,
                        "{}",
                        now,
                        RAG_SCHEMA_VERSION,
                    ),
                )
                await self._db.execute(
                    """INSERT INTO rag_chunks_fts
                       (text, chunk_id, document_id, collection_id, rel_path)
                       VALUES (?, ?, ?, ?, ?)""",
                    (text, chunk_id, document_id, collection_id, rel_path),
                )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise
        return len(chunk_records)

    async def mark_document_failed(
        self,
        *,
        collection_id: str,
        source_path: str,
        rel_path: str,
        file_hash: str,
        size_bytes: int,
        mtime: float,
        parser: str,
        error: str,
        mime: str = "application/octet-stream",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        assert self._db is not None
        document_id = _stable_id("document", collection_id, source_path)
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            await self._db.execute(
                "DELETE FROM rag_chunks_fts WHERE document_id = ?",
                (document_id,),
            )
            await self._db.execute(
                "DELETE FROM rag_chunks WHERE document_id = ?",
                (document_id,),
            )
            await self._db.execute(
                """INSERT OR REPLACE INTO rag_documents
                   (id, collection_id, source_path, rel_path, mime, title, hash,
                    size_bytes, mtime, parser, parser_status, parser_error,
                    metadata_json, updated_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_id,
                    collection_id,
                    source_path,
                    rel_path,
                    mime,
                    title,
                    file_hash,
                    size_bytes,
                    mtime,
                    parser,
                    RagDocumentStatus.failed.value,
                    error,
                    _json_dumps(metadata or {}),
                    time.time(),
                    RAG_SCHEMA_VERSION,
                ),
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    async def remove_document(self, document_id: str) -> None:
        assert self._db is not None
        await self._db.execute("BEGIN IMMEDIATE")
        try:
            await self._db.execute(
                "DELETE FROM rag_chunks_fts WHERE document_id = ?",
                (document_id,),
            )
            await self._db.execute(
                "DELETE FROM rag_chunks WHERE document_id = ?",
                (document_id,),
            )
            await self._db.execute(
                "DELETE FROM rag_documents WHERE id = ?",
                (document_id,),
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    async def search(
        self,
        query: str,
        opts: RagSearchOpts,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> list[RagSearchResult]:
        """Search indexed chunks. PR 2 implements FTS-only ranking."""

        del vector_weight, text_weight
        assert self._db is not None
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []
        sql = """
        SELECT f.chunk_id, c.document_id, c.collection_id, d.rel_path, d.title,
               d.mime, c.start_line, c.end_line, c.page, c.section, c.text,
               bm25(rag_chunks_fts) AS rank, c.metadata_json
        FROM rag_chunks_fts f
        JOIN rag_chunks c ON c.id = f.chunk_id
        JOIN rag_documents d ON d.id = c.document_id
        WHERE rag_chunks_fts MATCH ?
          AND d.parser_status = ?
        """
        params: list[Any] = [fts_query, RagDocumentStatus.indexed.value]
        if opts.collection_id:
            sql += " AND c.collection_id = ?"
            params.append(opts.collection_id)
        if opts.path_prefix:
            prefix = opts.path_prefix.rstrip("/")
            sql += " AND (d.rel_path = ? OR d.rel_path LIKE ?)"
            params.extend([prefix, f"{prefix}/%"])
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(1, opts.max_results * 3))
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()

        strict_results: list[RagSearchResult] = []
        relaxed_results: list[RagSearchResult] = []
        for row in rows:
            score = _bm25_to_score(float(row[11]))
            result = RagSearchResult(
                chunk_id=row[0],
                document_id=row[1],
                collection_id=row[2],
                path=row[3],
                title=row[4],
                mime=row[5],
                start_line=row[6],
                end_line=row[7],
                page=row[8],
                section=row[9],
                snippet=str(row[10])[:700],
                text=row[10],
                score=score,
                text_score=score,
                citation=_citation(row[3], row[6], row[7]),
                metadata=_json_loads_dict(row[12]),
            )
            relaxed_results.append(result)
            if score >= opts.min_score:
                strict_results.append(result)
        return (strict_results or relaxed_results)[: opts.max_results]

    async def show(self, selector: RagShowSelector) -> dict[str, Any]:
        assert self._db is not None
        if selector.chunk_id:
            return await self._show_chunk(selector)
        return await self._show_document(selector)

    async def _show_chunk(self, selector: RagShowSelector) -> dict[str, Any]:
        assert self._db is not None
        async with self._db.execute(
            """SELECT c.id, c.document_id, c.collection_id, d.rel_path, d.title,
                      d.mime, c.chunk_index, c.text, c.start_line, c.end_line,
                      c.page, c.section, c.metadata_json
               FROM rag_chunks c
               JOIN rag_documents d ON d.id = c.document_id
               WHERE c.id = ?""",
            (selector.chunk_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"found": False}
        text = _slice_lines(row[7], selector.from_line, selector.lines)
        return {
            "found": True,
            "kind": "chunk",
            "chunk": {
                "id": row[0],
                "document_id": row[1],
                "collection_id": row[2],
                "path": row[3],
                "title": row[4],
                "mime": row[5],
                "chunk_index": row[6],
                "text": text,
                "start_line": row[8],
                "end_line": row[9],
                "page": row[10],
                "section": row[11],
                "metadata": _json_loads_dict(row[12]),
            },
        }

    async def _show_document(self, selector: RagShowSelector) -> dict[str, Any]:
        assert self._db is not None
        sql = """
        SELECT id, collection_id, source_path, rel_path, mime, title, hash,
               size_bytes, mtime, parser, parser_status, parser_error,
               metadata_json, updated_at
        FROM rag_documents
        WHERE 1 = 1
        """
        params: list[Any] = []
        if selector.document_id:
            sql += " AND id = ?"
            params.append(selector.document_id)
        elif selector.path:
            sql += " AND rel_path = ?"
            params.append(selector.path)
            if selector.collection_id:
                sql += " AND collection_id = ?"
                params.append(selector.collection_id)
        else:
            raise ValueError("show selector requires document_id, chunk_id, or path")
        sql += " ORDER BY collection_id, rel_path LIMIT 1"
        async with self._db.execute(sql, params) as cur:
            document = await cur.fetchone()
        if document is None:
            return {"found": False}

        async with self._db.execute(
            """SELECT id, chunk_index, text, start_line, end_line, page, section,
                      metadata_json
               FROM rag_chunks
               WHERE document_id = ?
               ORDER BY chunk_index""",
            (document[0],),
        ) as cur:
            chunk_rows = await cur.fetchall()
        chunks = [
            {
                "id": row[0],
                "chunk_index": row[1],
                "text": row[2],
                "start_line": row[3],
                "end_line": row[4],
                "page": row[5],
                "section": row[6],
                "metadata": _json_loads_dict(row[7]),
            }
            for row in chunk_rows
        ]
        text = "\n\n".join(chunk["text"] for chunk in chunks)
        return {
            "found": True,
            "kind": "document",
            "document": {
                "id": document[0],
                "collection_id": document[1],
                "source_path": document[2],
                "rel_path": document[3],
                "mime": document[4],
                "title": document[5],
                "hash": document[6],
                "size_bytes": document[7],
                "mtime": document[8],
                "parser": document[9],
                "parser_status": document[10],
                "parser_error": document[11],
                "metadata": _json_loads_dict(document[12]),
                "updated_at": document[13],
            },
            "text": _slice_lines(text, selector.from_line, selector.lines),
            "chunks": chunks,
        }
