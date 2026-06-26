"""Path helpers for local document RAG."""

from __future__ import annotations

from pathlib import Path


def _validate_file_name(value: str, *, label: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."}:
        raise ValueError(f"{label} must be a file name")
    if "/" in name or "\\" in name:
        raise ValueError(f"{label} must not contain path separators")
    if Path(name).name != name:
        raise ValueError(f"{label} must be a file name")
    return name


def _validate_path_segment(value: str, *, label: str) -> str:
    segment = str(value or "").strip()
    if not segment or segment in {".", ".."}:
        raise ValueError(f"{label} must be a path segment")
    if "/" in segment or "\\" in segment:
        raise ValueError(f"{label} must not contain path separators")
    return segment


def resolve_agent_rag_db(
    agent_id: str,
    state_dir: str | Path,
    db_name: str = "rag.db",
) -> Path:
    """Return the per-agent RAG database path without creating it."""

    safe_agent_id = _validate_path_segment(agent_id, label="agent_id")
    safe_db_name = _validate_file_name(db_name, label="db_name")
    return Path(state_dir).expanduser() / "agents" / safe_agent_id / safe_db_name


def resolve_source_root(path: str) -> Path:
    """Resolve a configured RAG source root."""

    return Path(path).expanduser().resolve()


def safe_relative_to_root(root: Path, candidate: Path) -> str:
    """Return candidate relative to root, rejecting paths outside root."""

    resolved_root = root.expanduser().resolve()
    resolved_candidate = candidate.expanduser().resolve()
    try:
        return str(resolved_candidate.relative_to(resolved_root))
    except ValueError as exc:
        raise ValueError(f"{resolved_candidate} is outside {resolved_root}") from exc
