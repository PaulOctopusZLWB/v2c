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
from personal_context_node.signed_event_store import _payload_parses, trust_status_for_event
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
            "select * from signed_events order by owner_id, owner_sequence, event_hash",
        )
        trusted_events, valid, invalid = _evaluate_and_write_trust(conn, rows)
        # verify is a CHECK: it diffs the trusted-set projection against the stored
        # materialized view (to catch DB-level tampering) and does NOT rebuild it.
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


def recompute_materialized_state(conn: sqlite3.Connection) -> list[SignedEvent]:
    """Order-independent trust assignment + materialized-view rebuild (§43).

    Both the live store (after each insert / import batch) and verification use the
    SAME deterministic algorithm, so trust and materialization never depend on the
    order events were received in.
    """
    from personal_context_node.signed_event_store import _rebuild_materialized_views

    rows = fetch_all(conn, "select * from signed_events order by owner_id, owner_sequence, event_hash")
    trusted_events, _valid, _invalid = _evaluate_and_write_trust(conn, rows)
    _rebuild_materialized_views(conn)
    return trusted_events


def _evaluate_and_write_trust(
    conn: sqlite3.Connection, rows: list[dict[str, object]]
) -> tuple[list[SignedEvent], int, int]:
    valid = 0
    invalid = 0
    trusted_events: list[SignedEvent] = []
    previous_hash_by_owner: dict[str, str] = {}
    previous_sequence_by_owner: dict[str, int] = {}
    closed_owner_sequence: dict[str, int] = {}
    trusted_object_versions: set[tuple[str, int]] = set()
    # Clear any stale 'trusted' marks from a previous recompute before re-deriving the set,
    # so the incremental per-row writes below can never transiently leave two rows trusted
    # for the same partial-unique-index key (e.g. a prior batch's now-superseded card while
    # this pass promotes a different one) — which would abort the insert/import transaction.
    conn.execute("update signed_events set trust_status = 'unverified', verified = 0")
    forked_event_hashes = _forked_event_hashes(rows)
    forked_event_hashes |= _object_version_conflict_hashes(rows)
    successor_rotations = _successor_rotation_index(rows)
    card_owner_by_id = _card_owner_index(rows, forked_event_hashes=forked_event_hashes)
    annotation_author_by_id = _annotation_author_index(rows, forked_event_hashes=forked_event_hashes)
    trusted_successor_profiles: set[str] = set()
    for row in rows:
        event_hash = _row_event_hash(row)
        event: SignedEvent | None = None
        trust_status = "rejected"
        try:
            event = _event_from_row(row)
            hash_fields_valid = (
                _hash_fields_valid(row, event)
                and _payload_json_consistent(row, event)
                and _columns_match_event(row, event)
            )
            signature_valid = verify_signed_event(event, str(row["public_key"]))
            not_forked = event_hash not in forked_event_hashes
            chain_fields_valid = _chain_fields_valid(
                event,
                previous_hash_by_owner=previous_hash_by_owner,
                previous_sequence_by_owner=previous_sequence_by_owner,
            )
            if not (not_forked and signature_valid and hash_fields_valid):
                trust_status = "rejected"
            elif not chain_fields_valid:
                trust_status = "dangling"
            elif trust_status_for_event(event=event, verified=True) == "unsupported" or not _payload_parses(event):
                # Fail-closed (§42): unknown encoding/payload version, or a payload that
                # does not parse against its declared v1 schema — kept but not materialized.
                trust_status = "unsupported"
            elif (
                _owner_not_closed(event, closed_owner_sequence=closed_owner_sequence)
                and _successor_chain_start_valid(
                    event, successor_rotations=successor_rotations, trusted_successor_profiles=trusted_successor_profiles
                )
                and _card_successor_authorized(
                    event,
                    card_owner_by_id=card_owner_by_id,
                    successor_rotations=successor_rotations,
                    trusted_successor_profiles=trusted_successor_profiles,
                )
                and _annotation_revocation_authorized(event, annotation_author_by_id=annotation_author_by_id)
                and _card_creation_authorized(event, card_owner_by_id=card_owner_by_id)
                and _payload_authority_matches_event(event)
                and _object_id_matches_payload(event)
            ):
                trust_status = "trusted"
            else:
                trust_status = "rejected"
        except Exception:
            trust_status = "rejected"

        if trust_status == "trusted" and event is not None:
            # Enforce the §27.1 single-trusted-version invariant across ALL owners and event
            # types: a cross-owner (object_id, object_version) collision that authority binding
            # did not already reject (e.g. annotation id reuse) must not put two rows in
            # 'trusted' and trip idx_signed_events_trusted_object_version. Deterministic
            # first-in-sorted-order (owner_id, owner_sequence, event_hash) wins.
            object_version_key = (event.object_id, event.object_version)
            if object_version_key in trusted_object_versions:
                trust_status = "rejected"
            else:
                trusted_object_versions.add(object_version_key)

        if trust_status in ("trusted", "unsupported") and event is not None:
            valid += 1
            # Both trusted and unsupported events occupy a valid chain position. Track
            # the chain by the signature-protected event values, not derived columns.
            previous_hash_by_owner[event.owner_id] = event_hash
            previous_sequence_by_owner[event.owner_id] = event.owner_sequence
            if trust_status == "trusted":
                trusted_events.append(event)
                if _is_matching_predecessor_profile(event, rotation=successor_rotations.get(event.owner_id)):
                    trusted_successor_profiles.add(event.owner_id)
                if event.event_type == "identity_key.rotated":
                    closed_owner_sequence[event.owner_id] = event.owner_sequence
            conn.execute(
                "update signed_events set verified = 1, trust_status = ? where event_hash = ?",
                (trust_status, event_hash),
            )
        else:
            invalid += 1
            verified_flag = 1 if trust_status == "dangling" else 0
            conn.execute(
                "update signed_events set verified = ?, trust_status = ? where event_hash = ?",
                (verified_flag, trust_status, event_hash),
            )
    return trusted_events, valid, invalid


def _event_from_row(row: dict[str, object]) -> SignedEvent:
    event_json = str(row.get("raw_event_json") or row.get("event_json") or "{}")
    try:
        event_payload = json.loads(event_json)
    except json.JSONDecodeError:
        event_payload = {}
    if event_payload:
        # Reconstruct from raw_event_json verbatim (the complete-event source of
        # truth, §31.1) — do NOT substitute the derived payload_json column, or a
        # tampered raw_event_json would escape re-verification and be re-exported.
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


def _payload_json_consistent(row: dict[str, object], event: SignedEvent) -> bool:
    # The derived payload_json column must match the authoritative raw_event_json
    # payload; a mismatch means one of the two was tampered after signing.
    try:
        stored = json.loads(str(row["payload_json"]))
    except (json.JSONDecodeError, TypeError):
        return False
    return stored == event.payload


def _hash_fields_valid(row: dict[str, object], event: SignedEvent) -> bool:
    expected = canonical_signing_body_hash(event)
    return (
        _row_event_hash(row) == expected
        and str(row.get("canonical_signing_body_hash") or expected) == expected
    )


def _chain_fields_valid(
    event: SignedEvent,
    *,
    previous_hash_by_owner: dict[str, str],
    previous_sequence_by_owner: dict[str, int],
) -> bool:
    # Use the signature-protected event values, not the derived DB columns.
    owner_id = event.owner_id
    sequence = event.owner_sequence
    prev_event_hash = event.prev_event_hash
    previous_hash = previous_hash_by_owner.get(owner_id)
    previous_sequence = previous_sequence_by_owner.get(owner_id)
    if sequence == 1:
        return prev_event_hash is None
    return previous_sequence == sequence - 1 and prev_event_hash == previous_hash


def _owner_not_closed(event: SignedEvent, *, closed_owner_sequence: dict[str, int]) -> bool:
    closed_at = closed_owner_sequence.get(event.owner_id)
    return closed_at is None or event.owner_sequence <= closed_at


def _authentic_event(row: dict[str, object]) -> SignedEvent | None:
    try:
        return _event_from_row(row)
    except Exception:
        return None


def _columns_match_event(row: dict[str, object], event: SignedEvent) -> bool:
    # Derived envelope columns must equal the signature-protected raw event; a
    # mismatch means a DB-level tamper of a derived column (the signed blob is intact).
    prev = row["prev_event_hash"]
    prev_value = str(prev) if prev is not None else None
    try:
        return (
            str(row["owner_id"]) == event.owner_id
            and int(row["owner_sequence"]) == event.owner_sequence
            and str(row["object_id"]) == event.object_id
            and int(row["object_version"]) == event.object_version
            and prev_value == event.prev_event_hash
        )
    except (TypeError, ValueError):
        return False


def _signature_from_row(row: dict[str, object]) -> dict[str, str]:
    return {
        "algorithm": str(row.get("signature_algorithm") or "Ed25519"),
        "public_key_id": str(row.get("public_key_id") or row.get("signer_did")),
        "value": str(row.get("signature_value") or row.get("signature")),
    }


def _row_event_hash(row: dict[str, object]) -> str:
    return str(row.get("event_hash") or row["event_id"])


def _owner_sequence_key(row: dict[str, object]) -> tuple[str, int]:
    # Group by the signature-protected owner/sequence so a tampered column cannot hide
    # a fork (§31.2.6); fall back to columns only for unparseable raw events.
    event = _authentic_event(row)
    if event is not None:
        return (event.owner_id, event.owner_sequence)
    return (str(row["owner_id"]), int(row["owner_sequence"]))


def _object_version_key(row: dict[str, object]) -> tuple[str, str, int]:
    # An object-version fork (§43.9) is scoped to one owner's object lineage, mirroring the
    # owner-scoped owner_sequence fork (§31.2.6). A different owner reusing the same object_id
    # is an id-collision, NOT a version fork of this owner's object — keying the conflict on
    # owner_id prevents a forged cross-owner event from collaterally rejecting the legitimate
    # owner's card/annotation. (Cross-owner collisions are resolved by owner-binding instead.)
    event = _authentic_event(row)
    if event is not None:
        return (event.owner_id, event.object_id, event.object_version)
    return (str(row["owner_id"] or row["signer_did"]), str(row["object_id"]), int(row["object_version"]))


def _forked_event_hashes(rows: list[dict[str, object]]) -> set[str]:
    hashes_by_owner_sequence: dict[tuple[str, int], set[str]] = {}
    for row in rows:
        hashes_by_owner_sequence.setdefault(_owner_sequence_key(row), set()).add(_row_event_hash(row))
    return {
        _row_event_hash(row)
        for row in rows
        if len(hashes_by_owner_sequence[_owner_sequence_key(row)]) > 1
    }


def _object_version_conflict_hashes(rows: list[dict[str, object]]) -> set[str]:
    hashes_by_object_version: dict[tuple[str, str, int], set[str]] = {}
    for row in rows:
        hashes_by_object_version.setdefault(_object_version_key(row), set()).add(_row_event_hash(row))
    return {
        _row_event_hash(row)
        for row in rows
        if len(hashes_by_object_version[_object_version_key(row)]) > 1
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


def _card_owner_index(rows: list[dict[str, object]], *, forked_event_hashes: set[str]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for row in rows:
        try:
            if _row_event_hash(row) in forked_event_hashes:
                continue
            event = _event_from_row(row)
            if event.event_type != "memory_card.created":
                continue
            if not verify_signed_event(event, str(row["public_key"])):
                continue
            if not _hash_fields_valid(row, event):
                continue
            if trust_status_for_event(event=event, verified=True) != "trusted":
                continue
            card = MemoryCard.model_validate(event.payload)
            # Bind each card_id to the FIRST creator in the deterministic row order
            # (owner_id, owner_sequence, event_hash). A later cross-owner card.created for
            # the same card_id is an id-collision and is rejected (see
            # _card_creation_authorized), so it can neither censor nor overwrite the bound card.
            owners.setdefault(card.card_id, card.owner_did)
        except Exception:
            continue
    return owners


def _annotation_author_index(rows: list[dict[str, object]], *, forked_event_hashes: set[str]) -> dict[str, str]:
    authors: dict[str, str] = {}
    for row in rows:
        try:
            if _row_event_hash(row) in forked_event_hashes:
                continue
            event = _event_from_row(row)
            if event.event_type != "memory_annotation.created":
                continue
            if not verify_signed_event(event, str(row["public_key"])):
                continue
            if not _hash_fields_valid(row, event):
                continue
            if trust_status_for_event(event=event, verified=True) != "trusted":
                continue
            annotation = MemoryAnnotation.model_validate(event.payload)
            authors[annotation.annotation_id] = annotation.author
        except Exception:
            continue
    return authors


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


def _card_successor_authorized(
    event: SignedEvent,
    *,
    card_owner_by_id: dict[str, str],
    successor_rotations: dict[str, IdentityRotationReference],
    trusted_successor_profiles: set[str],
) -> bool:
    target_card_id = _card_successor_target_id(event)
    if target_card_id is None:
        return True
    card_owner_did = card_owner_by_id.get(target_card_id)
    if card_owner_did is None:
        return True
    if event.owner_id == card_owner_did:
        return True
    rotation = successor_rotations.get(event.owner_id)
    return (
        rotation is not None
        and event.owner_id in trusted_successor_profiles
        and rotation.old_identity_id == card_owner_did
    )


def _card_creation_authorized(event: SignedEvent, *, card_owner_by_id: dict[str, str]) -> bool:
    # A memory_card.created may only establish a card_id not already bound to a different
    # owner (§11: a card belongs to exactly one owner; others may only annotate). The
    # card-owner index binds each card_id to one deterministic owner; a cross-owner event
    # reusing that card_id is id-squatting and is rejected so it cannot censor or overwrite
    # the bound owner's card.
    if event.event_type != "memory_card.created":
        return True
    try:
        card_id = MemoryCard.model_validate(event.payload).card_id
    except Exception:
        return True  # payload validity is enforced elsewhere
    bound_owner = card_owner_by_id.get(card_id)
    return bound_owner is None or bound_owner == event.owner_id


def _card_successor_target_id(event: SignedEvent) -> str | None:
    if event.event_type == "memory_card.revoked":
        return MemoryCardRevocation.model_validate(event.payload).card_id
    if event.event_type == "memory_card.metadata_updated":
        return MemoryCardMetadataUpdate.model_validate(event.payload).card_id
    if event.event_type == "memory_card.superseded":
        return MemoryCardSupersession.model_validate(event.payload).card_id
    return None


def _annotation_revocation_authorized(
    event: SignedEvent,
    *,
    annotation_author_by_id: dict[str, str],
) -> bool:
    if event.event_type != "memory_annotation.revoked":
        return True
    revocation = MemoryAnnotationRevocation.model_validate(event.payload)
    author_did = annotation_author_by_id.get(revocation.annotation_id)
    return author_did is None or event.owner_id == author_did


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
    if event.event_type == "identity_key.rotated":
        # A rotation must be signed by the OLD key (§41.1 rule 1).
        rotation = IdentityKeyRotation.model_validate(event.payload)
        return event.owner_id == rotation.old_identity_id
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
            expected[card.card_id] = _card_projection(
                card,
                current_version=event.object_version,
                source_event_hash=event.event_hash,
                status="active",
            )
    for event in trusted_events:
        if event.event_type == "memory_card.revoked":
            revocation = MemoryCardRevocation.model_validate(event.payload)
            if revocation.card_id in expected:
                expected[revocation.card_id]["status"] = "revoked"
                expected[revocation.card_id]["current_version"] = event.object_version
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
                expected[update.card_id]["current_version"] = event.object_version
                expected[update.card_id]["source_event_hash"] = event.event_hash
                expected[update.card_id]["updated_at"] = str(update.created_at)
        if event.event_type == "memory_card.superseded":
            supersession = MemoryCardSupersession.model_validate(event.payload)
            if supersession.card_id in expected:
                expected[supersession.card_id]["status"] = "superseded"
                expected[supersession.card_id]["current_version"] = event.object_version
                expected[supersession.card_id]["source_event_hash"] = event.event_hash
    mismatches = _memory_card_mismatches(conn, expected)
    mismatches += _memory_annotation_mismatches(conn, trusted_events, card_ids=set(expected))
    return mismatches


def _memory_card_mismatches(conn: sqlite3.Connection, expected: dict[str, dict[str, object]]) -> int:
    actual_rows = fetch_all(
        conn,
        """
        select card_id, current_version, owner_id, owner_did, claim_type, claim, source_type, confidence,
               observed_at, valid_from, valid_until, subject_json, evidence_refs_json, candidate_claim,
               visibility_json, tags_json, status, source_event_hash, updated_at
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


def _card_projection(
    card: MemoryCard,
    *,
    current_version: int,
    source_event_hash: str,
    status: str,
) -> dict[str, object]:
    return {
        "card_id": card.card_id,
        "current_version": current_version,
        "owner_id": card.owner_did,
        "owner_did": card.owner_did,
        "claim_type": card.claim_type,
        "claim": card.claim,
        "source_type": card.source_type,
        "confidence": card.confidence,
        "observed_at": card.observed_at,
        "valid_from": card.valid_from,
        "valid_until": card.valid_until,
        # These claim-bearing columns must be in the diff: tampering the subject,
        # evidence refs, or candidate_claim would otherwise pass verification (§33, §43).
        "subject_json": card.subject.model_dump_json(),
        "evidence_refs_json": json.dumps(
            [evidence.model_dump(mode="json") for evidence in card.evidence_refs], ensure_ascii=False, sort_keys=True
        ),
        "candidate_claim": card.candidate_claim,
        "visibility_json": json.dumps(card.visibility.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True),
        "tags_json": json.dumps(card.tags, ensure_ascii=False, sort_keys=True),
        "status": status,
        "source_event_hash": source_event_hash,
        "updated_at": card.updated_at or str(card.created_at),
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
        "trust_status": "alter table signed_events add column trust_status text not null default 'unverified'",
    }
    for column, sql in migrations.items():
        if column not in existing:
            conn.execute(sql)
    conn.execute(
        """
        create table if not exists memory_cards (
          card_id text primary key,
          current_version integer not null default 1,
          owner_id text not null default '',
          owner_did text not null,
          claim_type text not null,
          claim text not null,
          source_type text not null default 'confirmed_generated',
          confidence real,
          observed_at text,
          valid_from text,
          valid_until text,
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
        "current_version": "alter table memory_cards add column current_version integer not null default 1",
        "owner_id": "alter table memory_cards add column owner_id text not null default ''",
        "source_type": "alter table memory_cards add column source_type text not null default 'confirmed_generated'",
        "confidence": "alter table memory_cards add column confidence real",
        "observed_at": "alter table memory_cards add column observed_at text",
        "valid_from": "alter table memory_cards add column valid_from text",
        "valid_until": "alter table memory_cards add column valid_until text",
        "visibility_json": "alter table memory_cards add column visibility_json text not null default '{\"type\":\"private\"}'",
        "tags_json": "alter table memory_cards add column tags_json text not null default '[]'",
        "updated_at": "alter table memory_cards add column updated_at text not null default ''",
    }
    for column, sql in card_column_migrations.items():
        if column not in existing_card_columns:
            conn.execute(sql)
    conn.commit()
