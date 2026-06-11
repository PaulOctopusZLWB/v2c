from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import SignedEvent, verify_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class MemoryVerifyResult:
    total_events: int
    valid_events: int
    invalid_events: int


def verify_memory_events(*, config: AppConfig) -> MemoryVerifyResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _ensure_signed_event_columns(conn)
        rows = fetch_all(
            conn,
            """
            select event_id, event_type, signer_did, created_at, payload_json,
                   event_json, signature, public_key
            from signed_events
            order by event_id
            """,
        )
        valid = 0
        invalid = 0
        for row in rows:
            event = _event_from_row(row)
            if verify_signed_event(event, row["public_key"]):
                valid += 1
                conn.execute("update signed_events set verified = 1 where event_id = ?", (row["event_id"],))
            else:
                invalid += 1
                conn.execute("update signed_events set verified = 0 where event_id = ?", (row["event_id"],))
        conn.commit()
        return MemoryVerifyResult(total_events=len(rows), valid_events=valid, invalid_events=invalid)
    finally:
        conn.close()


def _event_from_row(row: dict[str, object]) -> SignedEvent:
    event_json = str(row.get("event_json") or "{}")
    try:
        event_payload = json.loads(event_json)
    except json.JSONDecodeError:
        event_payload = {}
    if event_payload:
        event_payload["payload"] = json.loads(str(row["payload_json"]))
        event_payload["signature"] = row["signature"]
        return SignedEvent.model_validate(event_payload)
    return SignedEvent(
        event_id=str(row["event_id"]),
        event_type=str(row["event_type"]),
        signer_did=str(row["signer_did"]),
        created_at=str(row["created_at"]),
        payload=json.loads(str(row["payload_json"])),
        signature=str(row["signature"]),
    )


def _ensure_signed_event_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(signed_events)").fetchall()}
    migrations = {
        "created_at": "alter table signed_events add column created_at text not null default ''",
        "event_json": "alter table signed_events add column event_json text not null default '{}'",
    }
    for column, sql in migrations.items():
        if column not in existing:
            conn.execute(sql)
    conn.commit()
