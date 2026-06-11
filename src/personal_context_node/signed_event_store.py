from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel

from personal_context_node.core.protocols.memory import (
    SignedEvent,
    canonical_signing_body_hash,
    create_signed_event,
    signing_body,
    verify_signed_event,
)


def create_chained_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    payload: BaseModel,
    signer_did: str,
) -> tuple[SignedEvent, str]:
    previous = conn.execute(
        """
        select event_hash, owner_sequence
        from signed_events
        where owner_id = ? and trust_status = 'trusted'
        order by owner_sequence desc
        limit 1
        """,
        (signer_did,),
    ).fetchone()
    owner_sequence = int(previous["owner_sequence"]) + 1 if previous else 1
    prev_event_hash = str(previous["event_hash"]) if previous else None
    return create_signed_event(
        event_type=event_type,
        payload=payload,
        signer_did=signer_did,
        owner_sequence=owner_sequence,
        prev_event_hash=prev_event_hash,
    )


def insert_signed_event(conn: sqlite3.Connection, *, event: SignedEvent, public_key: str) -> None:
    verified = verify_signed_event(event, public_key)
    event_hash = event.event_hash
    signing_body_json = json.dumps(signing_body(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw_event_json = event.model_dump_json()
    conn.execute(
        """
        insert into signed_events (
          event_hash, event_id, event_type, signer_did,
          owner_id, owner_sequence, prev_event_hash, envelope_version,
          object_id, object_version, payload_type, payload_encoding,
          created_at, payload_json, raw_event_json, signing_body_json,
          canonical_signing_body_hash, signature_algorithm, public_key_id,
          signature_value, trust_status, event_json, signature, public_key, verified
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_hash,
            event_hash,
            event.event_type,
            event.signer_did,
            event.owner_id,
            event.owner_sequence,
            event.prev_event_hash,
            event.envelope_version,
            event.object_id,
            event.object_version,
            event.payload_type,
            event.payload_encoding,
            event.created_at,
            json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
            raw_event_json,
            signing_body_json,
            canonical_signing_body_hash(event),
            event.signature.algorithm,
            event.signature.public_key_id,
            event.signature.value,
            "trusted" if verified else "rejected",
            raw_event_json,
            event.signature.value,
            public_key,
            1 if verified else 0,
        ),
    )
