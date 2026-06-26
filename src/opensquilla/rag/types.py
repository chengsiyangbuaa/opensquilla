"""Shared RAG type aliases."""

from __future__ import annotations

from typing import Literal, TypeAlias

RagRetrievalMode: TypeAlias = Literal["hybrid", "fts_only"]
RagScaleHint: TypeAlias = Literal["small", "medium", "large"]
