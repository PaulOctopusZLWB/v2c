from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel

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
    create_signed_event,
    signing_body,
    verify_signed_event,
)


SUPPORTED_EVENT_PAYLOAD_TYPES = {
    "memory_card.created": "memory_card.v1",
    "memory_card.metadata_updated": "memory_card_metadata_update.v1",
    "memory_card.revoked": "memory_card_revocation.v1",
    "memory_card.superseded": "memory_card_supersession.v1",
    "identity_profile.published": "identity_profile.v1",
    "identity_key.rotated": "identity_key_rotation.v1",
    "memory_annotation.created": "memory_annotation.v1",
    "memory_annotation.revoked": "memory_annotation_revocation.v1",
}


@dataclass(frozen=True)
class IdentityRotationReference:
    old_identity_id: str
    new_identity_id: str
    event_hash: str


def create_chained_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    payload: BaseModel,
    signer_did: str,
    private_key: Ed25519PrivateKey | None = None,
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
        private_key=private_key,
        owner_sequence=owner_sequence,
        prev_event_hash=prev_event_hash,
    )


def insert_signed_event(conn: sqlite3.Connection, *, event: SignedEvent, public_key: str) -> None:
    verified = verify_signed_event(event, public_key)
    trust_status = _trusted_or_rejected_status(conn, event=event, public_key=public_key)
    event_hash = event.event_hash
    if trust_status == "trusted" and _reject_owner_sequence_fork(conn, event=event):
        trust_status = "rejected"
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
    if trust_status == "trusted":
        _apply_trusted_event(conn, event=event)
        _promote_dangling_successors(conn, predecessor=event)


def _reject_owner_sequence_fork(conn: sqlite3.Connection, *, event: SignedEvent) -> bool:
    existing = conn.execute(
        """
        select event_hash
        from signed_events
        where owner_id = ?
          and owner_sequence = ?
          and trust_status = 'trusted'
          and event_hash != ?
        """,
        (event.owner_id, event.owner_sequence, event.event_hash),
    ).fetchone()
    if existing is None:
        return False
    conn.execute(
        """
        update signed_events
        set trust_status = 'rejected'
        where owner_id = ?
          and owner_sequence = ?
          and trust_status = 'trusted'
        """,
        (event.owner_id, event.owner_sequence),
    )
    _rebuild_materialized_views(conn)
    return True


def _rebuild_materialized_views(conn: sqlite3.Connection) -> None:
    conn.execute("delete from memory_annotations")
    conn.execute("delete from memory_cards")
    conn.execute("delete from identity_profiles")
    rows = conn.execute(
        """
        select raw_event_json
        from signed_events
        where trust_status = 'trusted'
        order by owner_sequence, event_hash
        """,
    ).fetchall()
    for row in rows:
        _apply_trusted_event(conn, event=SignedEvent.model_validate_json(str(row["raw_event_json"])))


def _apply_trusted_event(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    if event.event_type == "memory_card.created":
        _upsert_memory_card(conn, event=event)
        _apply_trusted_card_successors(conn, card_id=event.object_id)
        _activate_dangling_annotations(conn, target_card_id=event.object_id)
    if event.event_type == "memory_card.metadata_updated":
        _update_memory_card_metadata(conn, event=event)
    if event.event_type == "memory_card.revoked":
        _revoke_memory_card(conn, event=event)
    if event.event_type == "memory_card.superseded":
        _supersede_memory_card(conn, event=event)
    if event.event_type == "memory_annotation.created":
        _upsert_memory_annotation(conn, event=event)
    if event.event_type == "memory_annotation.revoked":
        _revoke_memory_annotation(conn, event=event)
    if event.event_type == "identity_profile.published":
        _upsert_identity_profile(conn, event=event)
    if event.event_type == "identity_key.rotated":
        IdentityKeyRotation.model_validate(event.payload)


def _promote_dangling_successors(conn: sqlite3.Connection, *, predecessor: SignedEvent) -> None:
    rows = conn.execute(
        """
        select raw_event_json, public_key
        from signed_events
        where owner_id = ?
          and owner_sequence = ?
          and prev_event_hash = ?
          and trust_status = 'dangling'
        order by event_hash
        """,
        (predecessor.owner_id, predecessor.owner_sequence + 1, predecessor.event_hash),
    ).fetchall()
    for row in rows:
        event = SignedEvent.model_validate_json(str(row["raw_event_json"]))
        public_key = str(row["public_key"])
        verified = verify_signed_event(event, public_key)
        trust_status = _trusted_or_rejected_status(conn, event=event, public_key=public_key)
        conn.execute(
            "update signed_events set trust_status = ?, verified = ? where event_hash = ?",
            (trust_status, 1 if verified else 0, event.event_hash),
        )
        if trust_status == "trusted":
            _apply_trusted_event(conn, event=event)
            _promote_dangling_successors(conn, predecessor=event)


def _trusted_or_rejected_status(conn: sqlite3.Connection, *, event: SignedEvent, public_key: str) -> str:
    verified = verify_signed_event(event, public_key)
    trust_status = trust_status_for_event(event=event, verified=verified)
    if trust_status == "trusted" and _event_hash_chain_dangling(conn, event=event):
        trust_status = "dangling"
    if trust_status == "trusted" and _event_after_existing_rotation(conn, event=event):
        trust_status = "rejected"
    if trust_status == "trusted" and _invalid_successor_chain_start(conn, event=event):
        trust_status = "rejected"
    if trust_status == "trusted" and _unauthorized_known_card_successor(conn, event=event):
        trust_status = "rejected"
    if trust_status == "trusted" and _unauthorized_known_annotation_revocation(conn, event=event):
        trust_status = "rejected"
    if trust_status == "trusted" and not _payload_authority_matches_event(event):
        trust_status = "rejected"
    if trust_status == "trusted" and not _object_id_matches_payload(event):
        trust_status = "rejected"
    return trust_status


def _event_hash_chain_dangling(conn: sqlite3.Connection, *, event: SignedEvent) -> bool:
    if event.owner_sequence == 1:
        return event.prev_event_hash is not None
    previous = conn.execute(
        """
        select event_hash
        from signed_events
        where owner_id = ?
          and owner_sequence = ?
          and trust_status = 'trusted'
        """,
        (event.owner_id, event.owner_sequence - 1),
    ).fetchone()
    return previous is None or str(previous["event_hash"]) != event.prev_event_hash


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


def _invalid_successor_chain_start(conn: sqlite3.Connection, *, event: SignedEvent) -> bool:
    rotation = _trusted_rotation_to_identity(conn, identity_id=event.owner_id)
    if rotation is None:
        return False
    if event.owner_sequence == 1:
        return not _is_matching_predecessor_profile(event, rotation=rotation)
    existing_profile = conn.execute(
        """
        select 1
        from identity_profiles
        where identity_id = ?
          and predecessor_identity_id = ?
          and predecessor_rotation_event_hash = ?
        """,
        (event.owner_id, rotation.old_identity_id, rotation.event_hash),
    ).fetchone()
    return existing_profile is None


def _trusted_rotation_to_identity(conn: sqlite3.Connection, *, identity_id: str) -> IdentityRotationReference | None:
    rows = conn.execute(
        """
        select event_hash, payload_json
        from signed_events
        where event_type = 'identity_key.rotated'
          and trust_status = 'trusted'
        """,
    ).fetchall()
    for row in rows:
        rotation = IdentityKeyRotation.model_validate_json(str(row["payload_json"]))
        if rotation.new_identity_id == identity_id:
            return IdentityRotationReference(
                old_identity_id=rotation.old_identity_id,
                new_identity_id=rotation.new_identity_id,
                event_hash=str(row["event_hash"]),
            )
    return None


def _is_matching_predecessor_profile(event: SignedEvent, *, rotation: IdentityRotationReference) -> bool:
    if event.event_type != "identity_profile.published":
        return False
    profile = IdentityProfile.model_validate(event.payload)
    return (
        profile.identity_id == rotation.new_identity_id
        and profile.predecessor is not None
        and profile.predecessor.identity_id == rotation.old_identity_id
        and profile.predecessor.rotation_event_hash == rotation.event_hash
    )


def _unauthorized_known_card_successor(conn: sqlite3.Connection, *, event: SignedEvent) -> bool:
    target_card_id = _card_successor_target_id(event)
    if target_card_id is None:
        return False
    row = conn.execute("select owner_did from memory_cards where card_id = ?", (target_card_id,)).fetchone()
    if row is None:
        return False
    return not _identity_can_modify_card(conn, actor_did=event.owner_id, card_owner_did=str(row["owner_did"]))


def _card_successor_target_id(event: SignedEvent) -> str | None:
    if event.event_type == "memory_card.revoked":
        return MemoryCardRevocation.model_validate(event.payload).card_id
    if event.event_type == "memory_card.metadata_updated":
        return MemoryCardMetadataUpdate.model_validate(event.payload).card_id
    if event.event_type == "memory_card.superseded":
        return MemoryCardSupersession.model_validate(event.payload).card_id
    return None


def _unauthorized_known_annotation_revocation(conn: sqlite3.Connection, *, event: SignedEvent) -> bool:
    if event.event_type != "memory_annotation.revoked":
        return False
    revocation = MemoryAnnotationRevocation.model_validate(event.payload)
    row = conn.execute(
        "select author_did from memory_annotations where annotation_id = ?",
        (revocation.annotation_id,),
    ).fetchone()
    if row is None:
        return False
    return event.owner_id != str(row["author_did"])


def _identity_can_modify_card(conn: sqlite3.Connection, *, actor_did: str, card_owner_did: str) -> bool:
    if actor_did == card_owner_did:
        return True
    row = conn.execute(
        """
        select 1
        from identity_profiles
        where identity_id = ?
          and predecessor_identity_id = ?
        """,
        (actor_did, card_owner_did),
    ).fetchone()
    return row is not None


def _payload_authority_matches_event(event: SignedEvent) -> bool:
    if event.event_type == "memory_card.created":
        card = MemoryCard.model_validate(event.payload)
        return event.owner_id == card.owner_did
    if event.event_type == "memory_card.revoked":
        revocation = MemoryCardRevocation.model_validate(event.payload)
        return event.owner_id == revocation.revoked_by
    if event.event_type == "memory_card.metadata_updated":
        update = MemoryCardMetadataUpdate.model_validate(event.payload)
        return event.owner_id == update.updated_by
    if event.event_type == "memory_card.superseded":
        supersession = MemoryCardSupersession.model_validate(event.payload)
        return event.owner_id == supersession.superseded_by
    if event.event_type == "memory_annotation.created":
        annotation = MemoryAnnotation.model_validate(event.payload)
        return event.owner_id == annotation.author
    if event.event_type == "memory_annotation.revoked":
        revocation = MemoryAnnotationRevocation.model_validate(event.payload)
        return event.owner_id == revocation.revoked_by
    return True


def _object_id_matches_payload(event: SignedEvent) -> bool:
    expected = _payload_object_id(event)
    return expected is None or event.object_id == expected


def _payload_object_id(event: SignedEvent) -> str | None:
    if event.event_type in {
        "memory_card.created",
        "memory_card.revoked",
        "memory_card.metadata_updated",
        "memory_card.superseded",
    }:
        return _card_successor_target_id(event) or MemoryCard.model_validate(event.payload).card_id
    if event.event_type == "memory_annotation.created":
        return MemoryAnnotation.model_validate(event.payload).annotation_id
    if event.event_type == "memory_annotation.revoked":
        return MemoryAnnotationRevocation.model_validate(event.payload).annotation_id
    if event.event_type == "identity_profile.published":
        return IdentityProfile.model_validate(event.payload).identity_id
    if event.event_type == "identity_key.rotated":
        return IdentityKeyRotation.model_validate(event.payload).old_identity_id
    return None


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
          card_id, current_version, owner_id, owner_did, claim_type, claim, source_type, confidence,
          observed_at, valid_from, valid_until, subject_json, evidence_refs_json,
          candidate_claim, visibility_json, tags_json, status, source_event_hash,
          created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(card_id) do update set
          current_version = excluded.current_version,
          owner_id = excluded.owner_id,
          owner_did = excluded.owner_did,
          claim_type = excluded.claim_type,
          claim = excluded.claim,
          source_type = excluded.source_type,
          confidence = excluded.confidence,
          observed_at = excluded.observed_at,
          valid_from = excluded.valid_from,
          valid_until = excluded.valid_until,
          subject_json = excluded.subject_json,
          evidence_refs_json = excluded.evidence_refs_json,
          candidate_claim = excluded.candidate_claim,
          visibility_json = excluded.visibility_json,
          tags_json = excluded.tags_json,
          status = excluded.status,
          source_event_hash = excluded.source_event_hash,
          created_at = excluded.created_at,
          updated_at = excluded.updated_at
        """,
        (
            card.card_id,
            event.object_version,
            card.owner_did,
            card.owner_did,
            card.claim_type,
            card.claim,
            card.source_type,
            card.confidence,
            card.observed_at,
            card.valid_from,
            card.valid_until,
            card.subject.model_dump_json(),
            json.dumps([evidence.model_dump(mode="json") for evidence in card.evidence_refs], ensure_ascii=False, sort_keys=True),
            card.candidate_claim,
            json.dumps(card.visibility.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True),
            json.dumps(card.tags, ensure_ascii=False, sort_keys=True),
            "active",
            event.event_hash,
            str(card.created_at),
            card.updated_at or str(card.created_at),
        ),
    )


def _update_memory_card_metadata(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    update = MemoryCardMetadataUpdate.model_validate(event.payload)
    conn.execute(
        """
        update memory_cards
        set current_version = ?,
            visibility_json = ?,
            tags_json = ?,
            source_event_hash = ?,
            updated_at = ?
        where card_id = ?
        """,
        (
            event.object_version,
            json.dumps(update.visibility.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True),
            json.dumps(update.tags, ensure_ascii=False, sort_keys=True),
            event.event_hash,
            str(update.created_at),
            update.card_id,
        ),
    )


def _apply_trusted_card_successors(conn: sqlite3.Connection, *, card_id: str) -> None:
    rows = conn.execute(
        """
        select raw_event_json
        from signed_events
        where trust_status = 'trusted'
          and object_id = ?
          and event_type in (
            'memory_card.metadata_updated',
            'memory_card.revoked',
            'memory_card.superseded'
          )
        order by owner_sequence, event_hash
        """,
        (card_id,),
    ).fetchall()
    for row in rows:
        successor = SignedEvent.model_validate_json(str(row["raw_event_json"]))
        if successor.event_type == "memory_card.metadata_updated":
            _update_memory_card_metadata(conn, event=successor)
        if successor.event_type == "memory_card.revoked":
            _revoke_memory_card(conn, event=successor)
        if successor.event_type == "memory_card.superseded":
            _supersede_memory_card(conn, event=successor)


def _revoke_memory_card(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    revocation = MemoryCardRevocation.model_validate(event.payload)
    conn.execute(
        """
        update memory_cards
        set current_version = ?,
            status = 'revoked',
            source_event_hash = ?
        where card_id = ?
        """,
        (event.object_version, event.event_hash, revocation.card_id),
    )


def _supersede_memory_card(conn: sqlite3.Connection, *, event: SignedEvent) -> None:
    supersession = MemoryCardSupersession.model_validate(event.payload)
    conn.execute(
        """
        update memory_cards
        set current_version = ?,
            status = 'superseded',
            source_event_hash = ?
        where card_id = ?
        """,
        (event.object_version, event.event_hash, supersession.card_id),
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
