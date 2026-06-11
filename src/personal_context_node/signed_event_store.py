from __future__ import annotations

import json
import sqlite3

from pydantic import BaseModel

from personal_context_node.core.protocols.memory import (
    IdentityProfile,
    IdentityKeyRotation,
    MemoryAnnotation,
    MemoryAnnotationRevocation,
    MemoryCard,
    MemoryCardRevocation,
    MemoryCardSupersession,
    SignedEvent,
    canonical_signing_body_hash,
    create_signed_event,
    signing_body,
    verify_signed_event,
)


SUPPORTED_EVENT_PAYLOAD_TYPES = {
    "memory_card.created": "memory_card.v1",
    "memory_card.revoked": "memory_card_revocation.v1",
    "memory_card.superseded": "memory_card_supersession.v1",
    "identity_profile.published": "identity_profile.v1",
    "identity_key.rotated": "identity_key_rotation.v1",
    "memory_annotation.created": "memory_annotation.v1",
    "memory_annotation.revoked": "memory_annotation_revocation.v1",
}


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
    trust_status = trust_status_for_event(event=event, verified=verified)
    if trust_status == "trusted" and _event_after_existing_rotation(conn, event=event):
        trust_status = "rejected"
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
            trust_status,
            raw_event_json,
            event.signature.value,
            public_key,
            1 if verified else 0,
        ),
    )
    if event.event_type == "memory_card.created" and trust_status == "trusted":
        _upsert_memory_card(conn, event=event)
        _activate_dangling_annotations(conn, target_card_id=event.object_id)
    if event.event_type == "memory_card.revoked" and trust_status == "trusted":
        _revoke_memory_card(conn, event=event)
    if event.event_type == "memory_card.superseded" and trust_status == "trusted":
        _supersede_memory_card(conn, event=event)
    if event.event_type == "memory_annotation.created" and trust_status == "trusted":
        _upsert_memory_annotation(conn, event=event)
    if event.event_type == "memory_annotation.revoked" and trust_status == "trusted":
        _revoke_memory_annotation(conn, event=event)
    if event.event_type == "identity_profile.published" and trust_status == "trusted":
        _upsert_identity_profile(conn, event=event)
    if event.event_type == "identity_key.rotated" and trust_status == "trusted":
        IdentityKeyRotation.model_validate(event.payload)


def _event_after_existing_rotation(conn: sqlite3.Connection, *, event: SignedEvent) -> bool:
    if event.event_type == "identity_key.rotated":
        return False
    rotation = conn.execute(
        """
        select owner_sequence
        from signed_events
        where owner_id = ?
          and event_type = 'identity_key.rotated'
          and trust_status = 'trusted'
        order by owner_sequence asc
        limit 1
        """,
        (event.owner_id,),
    ).fetchone()
    return rotation is not None and event.owner_sequence > int(rotation["owner_sequence"])


def trust_status_for_event(*, event: SignedEvent, verified: bool) -> str:
    if not verified:
        return "rejected"
    if event.payload_encoding != "plain":
        return "unsupported"
    if SUPPORTED_EVENT_PAYLOAD_TYPES.get(event.event_type) != event.payload_type:
        return "unsupported"
    return "trusted"


def _upsert_memory_card(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    card = MemoryCard.model_validate(event.payload)
    conn.execute(
        """
        insert into memory_cards (
          card_id, owner_did, claim_type, claim, subject_json, evidence_refs_json,
          candidate_claim, status, source_event_hash, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(card_id) do update set
          owner_did = excluded.owner_did,
          claim_type = excluded.claim_type,
          claim = excluded.claim,
          subject_json = excluded.subject_json,
          evidence_refs_json = excluded.evidence_refs_json,
          candidate_claim = excluded.candidate_claim,
          status = excluded.status,
          source_event_hash = excluded.source_event_hash,
          created_at = excluded.created_at
        """,
        (
            card.card_id,
            card.owner_did,
            card.claim_type,
            card.claim,
            card.subject.model_dump_json(),
            json.dumps([evidence.model_dump(mode="json") for evidence in card.evidence_refs], ensure_ascii=False, sort_keys=True),
            card.candidate_claim,
            "active",
            event.event_hash,
            str(card.created_at),
        ),
    )


def _revoke_memory_card(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    revocation = MemoryCardRevocation.model_validate(event.payload)
    conn.execute(
        """
        update memory_cards
        set status = 'revoked',
            source_event_hash = ?
        where card_id = ?
        """,
        (event.event_hash, revocation.card_id),
    )


def _supersede_memory_card(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    supersession = MemoryCardSupersession.model_validate(event.payload)
    conn.execute(
        """
        update memory_cards
        set status = 'superseded',
            source_event_hash = ?
        where card_id = ?
        """,
        (event.event_hash, supersession.card_id),
    )


def _upsert_memory_annotation(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    annotation = MemoryAnnotation.model_validate(event.payload)
    target_exists = (
        conn.execute(
            "select 1 from memory_cards where card_id = ?",
            (annotation.target_card_id,),
        ).fetchone()
        is not None
    )
    conn.execute(
        """
        insert into memory_annotations (
          annotation_id, target_card_id, author_did, annotation_type, body,
          status, source_event_hash, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(annotation_id) do update set
          target_card_id = excluded.target_card_id,
          author_did = excluded.author_did,
          annotation_type = excluded.annotation_type,
          body = excluded.body,
          status = excluded.status,
          source_event_hash = excluded.source_event_hash,
          created_at = excluded.created_at
        """,
        (
            annotation.annotation_id,
            annotation.target_card_id,
            annotation.author,
            annotation.annotation_type,
            annotation.body,
            "active" if target_exists else "dangling",
            event.event_hash,
            str(annotation.created_at),
        ),
    )


def _revoke_memory_annotation(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    revocation = MemoryAnnotationRevocation.model_validate(event.payload)
    conn.execute(
        """
        update memory_annotations
        set status = 'revoked',
            source_event_hash = ?
        where annotation_id = ?
        """,
        (event.event_hash, revocation.annotation_id),
    )


def _activate_dangling_annotations(conn: sqlite3.Connection, *, target_card_id: str) -> None:
    conn.execute(
        "update memory_annotations set status = 'active' where target_card_id = ? and status = 'dangling'",
        (target_card_id,),
    )


def _upsert_identity_profile(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    profile = IdentityProfile.model_validate(event.payload)
    predecessor_identity_id = profile.predecessor.identity_id if profile.predecessor else None
    predecessor_rotation_event_hash = profile.predecessor.rotation_event_hash if profile.predecessor else None
    conn.execute(
        """
        insert into identity_profiles (
          identity_id, display_name, public_key_algorithm, public_key_multibase,
          predecessor_identity_id, predecessor_rotation_event_hash,
          source_event_hash, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(identity_id) do update set
          display_name = excluded.display_name,
          public_key_algorithm = excluded.public_key_algorithm,
          public_key_multibase = excluded.public_key_multibase,
          predecessor_identity_id = excluded.predecessor_identity_id,
          predecessor_rotation_event_hash = excluded.predecessor_rotation_event_hash,
          source_event_hash = excluded.source_event_hash,
          updated_at = excluded.updated_at
        """,
        (
            profile.identity_id,
            profile.display_name,
            profile.public_key_algorithm,
            profile.public_key_multibase,
            predecessor_identity_id,
            predecessor_rotation_event_hash,
            event.event_hash,
            str(profile.created_at),
            event.created_at,
        ),
    )
