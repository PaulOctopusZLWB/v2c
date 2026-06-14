from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import SignedEvent
from personal_context_node.memory_verify import recompute_materialized_state
from personal_context_node.signed_event_store import _insert_raw_signed_event
from personal_context_node.storage.sqlite import connect, initialize


@dataclass(frozen=True)
class MemoryImportResult:
    events_imported: int
    trusted_events: int
    rejected_events: int
    unsupported_events: int


def import_memory_events(*, config: AppConfig, input_path: Path, public_key: str | None = None) -> MemoryImportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        # Insert all events first, then recompute trust + materialization ONCE over the
        # whole set. Trust is an order-independent function of the trusted set (§43), so a
        # multi-owner JSONL imports identically regardless of line order, and a forged
        # cross-owner event cannot be trusted by arriving before its target.
        events: list[SignedEvent] = []
        for line in input_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(SignedEvent.model_validate_json(line))
        for event in events:
            _insert_raw_signed_event(conn, event=event, public_key=public_key)
        recompute_materialized_state(conn)
        imported = 0
        trusted = 0
        rejected = 0
        unsupported = 0
        for event in events:
            row = conn.execute(
                "select trust_status from signed_events where event_hash = ?",
                (event.event_hash,),
            ).fetchone()
            imported += 1
            trust_status = row["trust_status"] if row is not None else "rejected"
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
