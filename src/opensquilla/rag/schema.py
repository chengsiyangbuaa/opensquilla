"""SQLite schema for local document RAG."""

from __future__ import annotations

RAG_SCHEMA_VERSION = 1
RAG_META_SCHEMA_KEY = "rag_schema_version"

DDL_META = """
CREATE TABLE IF NOT EXISTS rag_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
"""

DDL_COLLECTIONS = """
CREATE TABLE IF NOT EXISTS rag_collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    include_json TEXT NOT NULL,
    exclude_json TEXT NOT NULL,
    scale_hint TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    watch INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
"""

DDL_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS rag_documents (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    mime TEXT NOT NULL,
    title TEXT,
    hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime REAL NOT NULL,
    parser TEXT NOT NULL,
    parser_status TEXT NOT NULL,
    parser_error TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(collection_id, source_path)
);
"""

DDL_CHUNKS = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    hash TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    page INTEGER,
    section TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
"""

DDL_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
    text,
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    collection_id UNINDEXED,
    rel_path UNINDEXED,
    tokenize='unicode61'
);
"""

DDL_EMBEDDING_CACHE = """
CREATE TABLE IF NOT EXISTS rag_embedding_cache (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    hash TEXT NOT NULL,
    embedding TEXT NOT NULL,
    dims INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(provider, model, provider_key, hash)
);
"""

DDL_INDEX_JOBS = """
CREATE TABLE IF NOT EXISTS rag_index_jobs (
    id TEXT PRIMARY KEY,
    collection_id TEXT,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    indexed_documents INTEGER NOT NULL DEFAULT 0,
    skipped_documents INTEGER NOT NULL DEFAULT 0,
    failed_documents INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    schema_version INTEGER NOT NULL DEFAULT 1
);
"""

DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_rag_documents_collection
ON rag_documents(collection_id);

CREATE INDEX IF NOT EXISTS idx_rag_documents_rel_path
ON rag_documents(collection_id, rel_path);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_document
ON rag_chunks(document_id);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_collection
ON rag_chunks(collection_id);
"""
