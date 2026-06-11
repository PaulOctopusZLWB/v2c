from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    IdentityProfile,
    IdentityKeyRotation,
    MemoryAnnotation,
    MemoryAnnotationRevocation,
    MemoryCard,
    MemoryCardMetadataUpdate,
    MemoryCardRevocation,
    MemoryCardSupersession,
    SignedEvent,
    canonical_signing_body_hash,
    verify_signed_event,
)
from personal_context_node.signed_event_store import trust_status_for_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class MemoryVerifyResult:
    total_events: int
    valid_events: int
    invalid_events: int
    materialization_mismatches: int = 0


@dataclass(frozen=True)
class IdentityRotationReference:
    old_identity_id: str
    new_identity_id: str
    event_hash: str


def verify_memory_events(*, config: AppConfig) -> MemoryVerifyResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _ensure_signed_event_columns(conn)
        rows = fetch_all(
            conn,
            """
            select *
            from signed_events
            order by owner_id, owner_sequence, event_hash
            """,
        )
        valid = 0
        invalid = 0
        trusted_events: list[SignedEvent] = []
        previous_hash_by_owner: dict[str, str] = {}
        previous_sequence_by_owner: dict[str, int] = {}
        closed_owner_sequence: dict[str, int] = {}
        forked_event_hashes = _forked_event_hashes(rows)
        successor_rotations = _successor_rotation_index(rows)
        trusted_successor_profiles: set[str] = set()
        for row in rows:
            event_hash = _row_event_hash(row)
            try:
                event = _event_from_row(row)
                row_valid = (
                    event_hash not in forked_event_hashes
                    and verify_signed_event(event, str(row["public_key"]))
                    and _hash_fields_valid(row, event)
                    and _chain_fields_valid(
                        row,
                        previous_hash_by_owner=previous_hash_by_owner,
                        previous_sequence_by_owner=previous_sequence_by_owner,
                    )
                    and _owner_not_closed(row, closed_owner_sequence=closed_owner_sequence)
                    and _successor_chain_start_valid(
                        event,
                        successor_rotations=successor_rotations,
                        trusted_successor_profiles=trusted_successor_profiles,
                    )
                )
            except Exception:
                row_valid = False
            if row_valid:
                valid += 1
                trust_status = trust_status_for_event(event=event, verified=True)
                if trust_status == "trusted":
                    trusted_events.append(event)
                    if _is_matching_predecessor_profile(
                        event,
                        rotation=successor_rotations.get(event.owner_id),
                    ):
                        trusted_successor_profiles.add(event.owner_id)
                previous_hash_by_owner[str(row["owner_id"])] = event_hash
                previous_sequence_by_owner[str(row["owner_id"])] = int(row["owner_sequence"])
                conn.execute(
                    "update signed_events set verified = 1, trust_status = ? where event_hash = ?",
                    (trust_status, event_hash),
                )
                if trust_status == "trusted" and event.event_type == "identity_key.rotated":
                    IdentityKeyRotation.model_validate(event.payload)
                    closed_owner_sequence[str(row["owner_id"])] = int(row["owner_sequence"])
            else:
                invalid += 1
                conn.execute(
                    "update signed_events set verified = 0, trust_status = 'rejected' where event_hash = ?",
                    (event_hash,),
                )
        mismatches = _materialization_mismatches(conn, trusted_events)
        conn.commit()
        return MemoryVerifyResult(
            total_events=len(rows),
            valid_events=valid,
            invalid_events=invalid,
            materialization_mismatches=mismatches,
        )
    finally:
        conn.close()


def _event_from_row(row: dict[str, object]) -> SignedEvent:
    event_json = str(row.get("raw_event_json") or row.get("event_json") or "{}")
    try:
        event_payload = json.loads(event_json)
    except json.JSONDecodeError:
        event_payload = {}
    if event_payload:
        event_payload["payload"] = json.loads(str(row["payload_json"]))
        if isinstance(event_payload.get("signature"), str):
            event_payload["signature"] = _signature_from_row(row)
        return SignedEvent.model_validate(event_payload)
    return SignedEvent(
        envelope_version=str(row.get("envelope_version") or "signed_event.v1"),
        event_type=str(row["event_type"]),
        object_id=str(row["object_id"]),
        object_version=int(row["object_version"]),
        owner_id=str(row["owner_id"] or row["signer_did"]),
        owner_sequence=int(row["owner_sequence"]),
        prev_event_hash=str(row["prev_event_hash"]) if row["prev_event_hash"] is not None else None,
        payload_type=str(row["payload_type"]),
        payload_encoding=str(row["payload_encoding"]),
        created_at=str(row["created_at"]),
        payload=json.loads(str(row["payload_json"])),
        signature=_signature_from_row(row),
    )


def _hash_fields_valid(row: dict[str, object], event: SignedEvent) -> bool:
    expected = canonical_signing_body_hash(event)
    return (
        _row_event_hash(row) == expected
        and str(row.get("canonical_signing_body_hash") or expected) == expected
    )


def _chain_fields_valid(
    row: dict[str, object],
    *,
    previous_hash_by_owner: dict[str, str],
    previous_sequence_by_owner: dict[str, int],
) -> bool:
    owner_id = str(row["owner_id"])
    sequence = int(row["owner_sequence"])
    prev_event_hash = row["prev_event_hash"]
    previous_hash = previous_hash_by_owner.get(owner_id)
    previous_sequence = previous_sequence_by_owner.get(owner_id)
    if sequence == 1:
        return prev_event_hash is None
    return previous_sequence == sequence - 1 and prev_event_hash == previous_hash


def _owner_not_closed(row: dict[str, object], *, closed_owner_sequence: dict[str, int]) -> bool:
    owner_id = str(row["owner_id"])
    sequence = int(row["owner_sequence"])
    closed_at = closed_owner_sequence.get(owner_id)
    return closed_at is None or sequence <= closed_at


def _signature_from_row(row: dict[str, object]) -> dict[str, str]:
    return {
        "algorithm": str(row.get("signature_algorithm") or "Ed25519"),
        "public_key_id": str(row.get("public_key_id") or row.get("signer_did")),
        "value": str(row.get("signature_value") or row.get("signature")),
    }


def _row_event_hash(row: dict[str, object]) -> str:
    return str(row.get("event_hash") or row["event_id"])


def _forked_event_hashes(rows: list[dict[str, object]]) -> set[str]:
    hashes_by_owner_sequence: dict[tuple[str, int], set[str]] = {}
    for row in rows:
        key = (str(row["owner_id"]), int(row["owner_sequence"]))
        hashes_by_owner_sequence.setdefault(key, set()).add(_row_event_hash(row))
    return {
        event_hash
        for row in rows
        if len(hashes_by_owner_sequence[(str(row["owner_id"]), int(row["owner_sequence"]))]) > 1
        for event_hash in [_row_event_hash(row)]
    }


def _successor_rotation_index(rows: list[dict[str, object]]) -> dict[str, IdentityRotationReference]:
    rotations: dict[str, IdentityRotationReference] = {}
    for row in rows:
        try:
            event = _event_from_row(row)
            if event.event_type != "identity_key.rotated":
                continue
            if not verify_signed_event(event, str(row["public_key"])):
                continue
            if not _hash_fields_valid(row, event):
                continue
            if trust_status_for_event(event=event, verified=True) != "trusted":
                continue
            rotation = IdentityKeyRotation.model_validate(event.payload)
            rotations[rotation.new_identity_id] = IdentityRotationReference(
                old_identity_id=rotation.old_identity_id,
                new_identity_id=rotation.new_identity_id,
                event_hash=_row_event_hash(row),
            )
        except Exception:
            continue
    return rotations


def _successor_chain_start_valid(
    event: SignedEvent,
    *,
    successor_rotations: dict[str, IdentityRotationReference],
    trusted_successor_profiles: set[str],
) -> bool:
    rotation = successor_rotations.get(event.owner_id)
    if rotation is None:
        return True
    if event.owner_sequence == 1:
        return _is_matching_predecessor_profile(event, rotation=rotation)
    return event.owner_id in trusted_successor_profiles


def _is_matching_predecessor_profile(event: SignedEvent, *, rotation: IdentityRotationReference | None) -> bool:
    if rotation is None or event.event_type != "identity_profile.published":
        return False
    profile = IdentityProfile.model_validate(event.payload)
    return (
        profile.identity_id == rotation.new_identity_id
        and profile.predecessor is not None
        and profile.predecessor.identity_id == rotation.old_identity_id
        and profile.predecessor.rotation_event_hash == rotation.event_hash
    )


def _materialization_mismatches(conn: sqlite3.Connection, trusted_events: list[SignedEvent]) -> int:
    expected: dict[str, dict[str, object]] = {}
    for event in trusted_events:
        if event.event_type == "memory_card.created":
            card = MemoryCard.model_validate(event.payload)
            expected[card.card_id] = _card_projection(card, source_event_hash=event.event_hash, status="active")
    for event in trusted_events:
        if event.event_type == "memory_card.revoked":
            revocation = MemoryCardRevocation.model_validate(event.payload)
            if revocation.card_id in expected:
                expected[revocation.card_id]["status"] = "revoked"
                expected[revocation.card_id]["source_event_hash"] = event.event_hash
        if event.event_type == "memory_card.metadata_updated":
            update = MemoryCardMetadataUpdate.model_validate(event.payload)
            if update.card_id in expected:
                expected[update.card_id]["visibility_json"] = json.dumps(
                    update.visibility.model_dump(mode="json", exclude_none=True),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                expected[update.card_id]["tags_json"] = json.dumps(update.tags, ensure_ascii=False, sort_keys=True)
                expected[update.card_id]["source_event_hash"] = event.event_hash
        if event.event_type == "memory_card.superseded":
            supersession = MemoryCardSupersession.model_validate(event.payload)
            if supersession.card_id in expected:
                expected[supersession.card_id]["status"] = "superseded"
                expected[supersession.card_id]["source_event_hash"] = event.event_hash
    mismatches = _memory_card_mismatches(conn, expected)
    mismatches += _memory_annotation_mismatches(conn, trusted_events, card_ids=set(expected))
    return mismatches


def _memory_card_mismatches(conn: sqlite3.Connection, expected: dict[str, dict[str, object]]) -> int:
    actual_rows = fetch_all(
        conn,
        """
        select card_id, owner_did, claim_type, claim, visibility_json, tags_json, status, source_event_hash
        from memory_cards
        order by card_id
        """,
    )
    actual = {str(row["card_id"]): row for row in actual_rows}
    mismatches = 0
    for card_id, expected_row in expected.items():
        actual_row = actual.pop(card_id, None)
        if actual_row != expected_row:
            mismatches += 1
    mismatches += len(actual)
    return mismatches


def _memory_annotation_mismatches(
    conn: sqlite3.Connection,
    trusted_events: list[SignedEvent],
    *,
    card_ids: set[str],
) -> int:
    expected: dict[str, dict[str, object]] = {}
    for event in trusted_events:
        if event.event_type == "memory_annotation.created":
            annotation = MemoryAnnotation.model_validate(event.payload)
            expected[annotation.annotation_id] = {
                "annotation_id": annotation.annotation_id,
                "target_card_id": annotation.target_card_id,
                "author_did": annotation.author,
                "annotation_type": annotation.annotation_type,
                "body": annotation.body,
                "status": "active" if annotation.target_card_id in card_ids else "dangling",
                "source_event_hash": event.event_hash,
            }
        if event.event_type == "memory_annotation.revoked":
            revocation = MemoryAnnotationRevocation.model_validate(event.payload)
            if revocation.annotation_id in expected:
                expected[revocation.annotation_id]["status"] = "revoked"
                expected[revocation.annotation_id]["source_event_hash"] = event.event_hash
    actual_rows = fetch_all(
        conn,
        """
        select annotation_id, target_card_id, author_did, annotation_type, body, status, source_event_hash
        from memory_annotations
        order by annotation_id
        """,
    )
    actual = {str(row["annotation_id"]): row for row in actual_rows}
    mismatches = 0
    for annotation_id, expected_row in expected.items():
        actual_row = actual.pop(annotation_id, None)
        if actual_row != expected_row:
            mismatches += 1
    mismatches += len(actual)
    return mismatches


def _card_projection(card: MemoryCard, *, source_event_hash: str, status: str) -> dict[str, object]:
    return {
        "card_id": card.card_id,
        "owner_did": card.owner_did,
        "claim_type": card.claim_type,
        "claim": card.claim,
        "visibility_json": json.dumps(card.visibility.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True),
        "tags_json": json.dumps(card.tags, ensure_ascii=False, sort_keys=True),
        "status": status,
        "source_event_hash": source_event_hash,
    }


def _ensure_signed_event_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(signed_events)").fetchall()}
    migrations = {
        "created_at": "alter table signed_events add column created_at text not null default ''",
        "event_json": "alter table signed_events add column event_json text not null default '{}'",
        "event_hash": "alter table signed_events add column event_hash text",
        "owner_id": "alter table signed_events add column owner_id text",
        "owner_sequence": "alter table signed_events add column owner_sequence integer",
        "prev_event_hash": "alter table signed_events add column prev_event_hash text",
        "envelope_version": "alter table signed_events add column envelope_version text",
        "object_id": "alter table signed_events add column object_id text",
        "object_version": "alter table signed_events add column object_version integer",
        "payload_type": "alter table signed_events add column payload_type text",
        "payload_encoding": "alter table signed_events add column payload_encoding text",
        "raw_event_json": "alter table signed_events add column raw_event_json text",
        "signing_body_json": "alter table signed_events add column signing_body_json text",
        "canonical_signing_body_hash": "alter table signed_events add column canonical_signing_body_hash text",
        "signature_algorithm": "alter table signed_events add column signature_algorithm text",
        "public_key_id": "alter table signed_events add column public_key_id text",
        "signature_value": "alter table signed_events add column signature_value text",
        "trust_status": "alter table signed_events add column trust_status text not null default 'trusted'",
    }
    for column, sql in migrations.items():
        if column not in existing:
            conn.execute(sql)
    conn.execute(
        """
        create table if not exists memory_cards (
          card_id text primary key,
          owner_did text not null,
          claim_type text not null,
          claim text not null,
          subject_json text not null,
          evidence_refs_json text not null,
          candidate_claim text,
          visibility_json text not null default '{"type":"private"}',
          tags_json text not null default '[]',
          status text not null,
          source_event_hash text not null,
          created_at text not null,
          updated_at text not null default ''
        )
        """
    )
    existing_card_columns = {row["name"] for row in conn.execute("pragma table_info(memory_cards)").fetchall()}
    card_column_migrations = {
        "visibility_json": "alter table memory_cards add column visibility_json text not null default '{\"type\":\"private\"}'",
        "tags_json": "alter table memory_cards add column tags_json text not null default '[]'",
        "updated_at": "alter table memory_cards add column updated_at text not null default ''",
    }
    for column, sql in card_column_migrations.items():
        if column not in existing_card_columns:
            conn.execute(sql)
    conn.commit()
