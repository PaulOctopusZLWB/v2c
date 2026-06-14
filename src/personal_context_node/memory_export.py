from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class MemoryExportResult:
    events_exported: int
    output_path: Path


def export_memory_events(*, config: AppConfig, output_path: Path, since: str) -> MemoryExportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select raw_event_json
            from signed_events
            where trust_status in ('trusted', 'unsupported', 'dangling') and created_at >= ?
            order by created_at, event_hash
            """,
            (since,),
        )
    finally:
        conn.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(output_path, "\n".join(str(row["raw_event_json"]) for row in rows) + ("\n" if rows else ""))
    return MemoryExportResult(events_exported=len(rows), output_path=output_path)
