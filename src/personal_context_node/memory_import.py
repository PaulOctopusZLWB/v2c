from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import SignedEvent
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, initialize


@dataclass(frozen=True)
class MemoryImportResult:
    events_imported: int
    trusted_events: int
    rejected_events: int
    unsupported_events: int


def import_memory_events(*, config: AppConfig, input_path: Path, public_key: str) -> MemoryImportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = 0
        trusted = 0
        rejected = 0
        unsupported = 0
        for line in input_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = SignedEvent.model_validate_json(line)
            insert_signed_event(conn, event=event, public_key=public_key)
            trust_status = conn.execute(
                "select trust_status from signed_events where event_hash = ?",
                (event.event_hash,),
            ).fetchone()["trust_status"]
            imported += 1
            if trust_status == "trusted":
                trusted += 1
            elif trust_status == "rejected":
                rejected += 1
            elif trust_status == "unsupported":
                unsupported += 1
        conn.commit()
        return MemoryImportResult(
            events_imported=imported,
            trusted_events=trusted,
            rejected_events=rejected,
            unsupported_events=unsupported,
        )
    finally:
        conn.close()
