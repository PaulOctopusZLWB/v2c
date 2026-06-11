from __future__ import annotations

from pathlib import Path

from personal_context_node.core.protocols.memory import IdentityProfile, create_signed_event, verify_signed_event
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_identity_profile_event_uses_identity_id_as_object_id_and_verifies() -> None:
    profile = IdentityProfile(
        identity_id="did:key:test-owner",
        display_name="Paul",
        public_key_multibase="z6MtestOwner",
    )

    event, public_key = create_signed_event(
        event_type="identity_profile.published",
        payload=profile,
        signer_did=profile.identity_id,
    )

    assert event.object_id == "did:key:test-owner"
    assert event.payload_type == "identity_profile.v1"
    assert verify_signed_event(event, public_key)


def test_trusted_identity_profile_event_materializes_profile(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        profile = IdentityProfile(
            identity_id="did:key:test-owner",
            display_name="Paul",
            public_key_multibase="z6MtestOwner",
        )
        event, public_key = create_signed_event(
            event_type="identity_profile.published",
            payload=profile,
            signer_did=profile.identity_id,
        )

        insert_signed_event(conn, event=event, public_key=public_key)

        rows = fetch_all(
            conn,
            """
            select identity_id, display_name, public_key_algorithm, public_key_multibase,
                   predecessor_identity_id, predecessor_rotation_event_hash, source_event_hash
            from identity_profiles
            """,
        )
    finally:
        conn.close()
    assert rows == [
        {
            "identity_id": "did:key:test-owner",
            "display_name": "Paul",
            "public_key_algorithm": "Ed25519",
            "public_key_multibase": "z6MtestOwner",
            "predecessor_identity_id": None,
            "predecessor_rotation_event_hash": None,
            "source_event_hash": event.event_hash,
        }
    ]


def test_identity_profile_materializes_predecessor_reference(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        profile = IdentityProfile(
            identity_id="did:key:new-owner",
            display_name="Paul",
            public_key_multibase="z6MnewOwner",
            predecessor={
                "identity_id": "did:key:old-owner",
                "rotation_event_hash": "sha256:old-rotation",
            },
        )
        event, public_key = create_signed_event(
            event_type="identity_profile.published",
            payload=profile,
            signer_did=profile.identity_id,
        )

        insert_signed_event(conn, event=event, public_key=public_key)

        rows = fetch_all(
            conn,
            """
            select predecessor_identity_id, predecessor_rotation_event_hash
            from identity_profiles
            where identity_id = ?
            """,
            ("did:key:new-owner",),
        )
    finally:
        conn.close()
    assert rows == [
        {
            "predecessor_identity_id": "did:key:old-owner",
            "predecessor_rotation_event_hash": "sha256:old-rotation",
        }
    ]
