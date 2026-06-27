"""Shared RAG type aliases."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

type RagRetrievalMode = Literal["hybrid", "fts_only"]
type RagScaleHint = Literal["small", "medium", "large"]

DEFAULT_RAG_SEARCH_RESULTS = 8
DEFAULT_RAG_SEARCH_MIN_SCORE = 0.35


class RagDocumentStatus(StrEnum):
    indexed = "indexed"
    skipped = "skipped"
    failed = "failed"
    removed = "removed"


class RagIndexJobStatus(StrEnum):
    running = "running"
    completed = "completed"
    failed = "failed"


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    text: str
    mime: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RagSearchOpts:
    max_results: int = DEFAULT_RAG_SEARCH_RESULTS
    min_score: float = DEFAULT_RAG_SEARCH_MIN_SCORE
    collection_id: str | None = None
    path_prefix: str | None = None


@dataclass(frozen=True)
class RagShowSelector:
    collection_id: str | None = None
    path: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    from_line: int | None = None
    lines: int | None = None


@dataclass
class RagSearchResult:
    chunk_id: str
    document_id: str
    collection_id: str
    path: str
    title: str | None
    mime: str
    start_line: int | None
    end_line: int | None
    page: int | None
    section: str | None
    snippet: str
    text: str
    score: float
    vector_score: float | None = None
    text_score: float | None = None
    citation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RagIndexSummary:
    job_id: str
    collection_id: str | None
    indexed_documents: int = 0
    skipped_documents: int = 0
    failed_documents: int = 0
    removed_documents: int = 0
    capped: bool = False
    errors: list[str] = field(default_factory=list)
