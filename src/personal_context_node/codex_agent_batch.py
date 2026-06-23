from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from personal_context_node.agent_session_types import AgentSessionDocument
from personal_context_node.agent_sessions import import_agent_session
from personal_context_node.codex_session_jsonl import parse_codex_session_jsonl
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


@dataclass(frozen=True)
class CodexBatchImportResult:
    files_found: int
    sessions_imported: int
    sessions_skipped: int
    turns_imported: int
    tool_events_imported: int
    evidence_refs_created: int
    index_days: list[str]
    imported_session_ids: list[str]
    skipped_session_ids: list[str]


def import_codex_batch(
    *,
    config: AppConfig,
    jsonl_paths: list[Path],
    jsonl_dirs: list[Path],
) -> CodexBatchImportResult:
    paths = discover_codex_jsonl_files(jsonl_paths=jsonl_paths, jsonl_dirs=jsonl_dirs)
    documents = [parse_codex_session_jsonl(path) for path in paths]
    _ensure_no_input_session_conflicts(documents)
    _ensure_no_existing_source_conflicts(config=config, documents=documents)

    sessions_imported = 0
    sessions_skipped = 0
    turns_imported = 0
    tool_events_imported = 0
    evidence_refs_created = 0
    imported_session_ids: list[str] = []
    skipped_session_ids: list[str] = []

    for document in documents:
        result = import_agent_session(config=config, document=document)
        sessions_imported += result.sessions_imported
        turns_imported += result.turns_imported
        tool_events_imported += result.tool_events_imported
        evidence_refs_created += result.evidence_refs_created
        if result.sessions_imported:
            imported_session_ids.append(result.agent_session_id)
        else:
            sessions_skipped += 1
            skipped_session_ids.append(result.agent_session_id)

    return CodexBatchImportResult(
        files_found=len(paths),
        sessions_imported=sessions_imported,
        sessions_skipped=sessions_skipped,
        turns_imported=turns_imported,
        tool_events_imported=tool_events_imported,
        evidence_refs_created=evidence_refs_created,
        index_days=_index_days(documents),
        imported_session_ids=imported_session_ids,
        skipped_session_ids=skipped_session_ids,
    )


def discover_codex_jsonl_files(*, jsonl_paths: list[Path], jsonl_dirs: list[Path]) -> list[Path]:
    candidates: list[Path] = []
    candidates.extend(jsonl_paths)
    for directory in jsonl_dirs:
        candidates.extend(sorted(directory.glob("*.jsonl")))

    seen: set[Path] = set()
    paths: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=True)
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(resolved)
    return sorted(paths)


def _ensure_no_input_session_conflicts(documents: list[AgentSessionDocument]) -> None:
    by_session_id: dict[str, AgentSessionDocument] = {}
    for document in documents:
        existing = by_session_id.get(document.session_id)
        if existing is None:
            by_session_id[document.session_id] = document
            continue
        if _same_source_identity(existing, document):
            continue
        raise ValueError(
            f"agent session source identity differs for {document.session_id}: "
            f"incoming source_path={document.source_path!r}, source_sha256={document.source_sha256!r}; "
            f"other source_path={existing.source_path!r}, source_sha256={existing.source_sha256!r}"
        )


def _ensure_no_existing_source_conflicts(*, config: AppConfig, documents: list[AgentSessionDocument]) -> None:
    if not documents or not config.database_path.exists():
        return
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for document in documents:
            row = conn.execute(
                """
                select agent_session_id, source_type, source_path, source_sha256
                from agent_sessions
                where agent_session_id = ?
                """,
                (document.session_id,),
            ).fetchone()
            if row is None or _row_matches_source_identity(row, document):
                continue
            raise ValueError(
                f"agent session source identity differs for {document.session_id}: "
                f"existing source_type={row['source_type']!r}, source_path={row['source_path']!r}, "
                f"source_sha256={row['source_sha256']!r}; incoming source_type={document.source_type!r}, "
                f"source_path={document.source_path!r}, source_sha256={document.source_sha256!r}"
            )
    finally:
        conn.close()


def _row_matches_source_identity(row: sqlite3.Row, document: AgentSessionDocument) -> bool:
    return (
        row["source_type"] == document.source_type
        and row["source_path"] == document.source_path
        and row["source_sha256"] == document.source_sha256
    )


def _same_source_identity(first: AgentSessionDocument, second: AgentSessionDocument) -> bool:
    return (
        first.source_type == second.source_type
        and first.source_path == second.source_path
        and first.source_sha256 == second.source_sha256
    )


def _index_days(documents: list[AgentSessionDocument]) -> list[str]:
    days: set[str] = set()
    for document in documents:
        day = document.started_at[:10]
        date.fromisoformat(day)
        days.add(day)
    return sorted(days)
