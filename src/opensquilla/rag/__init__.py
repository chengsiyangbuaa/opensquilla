"""Local document RAG support."""

from opensquilla.rag.paths import (
    resolve_agent_rag_db,
    resolve_source_root,
    safe_relative_to_root,
)
from opensquilla.rag.types import (
    ParsedDocument,
    RagDocumentStatus,
    RagIndexJobStatus,
    RagIndexSummary,
    RagRetrievalMode,
    RagScaleHint,
    RagSearchOpts,
    RagSearchResult,
    RagShowSelector,
)

__all__ = [
    "ParsedDocument",
    "RagDocumentStatus",
    "RagIndexJobStatus",
    "RagIndexSummary",
    "RagRetrievalMode",
    "RagScaleHint",
    "RagSearchOpts",
    "RagSearchResult",
    "RagShowSelector",
    "resolve_agent_rag_db",
    "resolve_source_root",
    "safe_relative_to_root",
]
