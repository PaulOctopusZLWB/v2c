from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_active_memory_annotations_view_excludes_non_active_targets_and_annotations(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_card(conn, card_id="mem_active", status="active")
        _insert_card(conn, card_id="mem_revoked", status="revoked")
        _insert_card(conn, card_id="mem_superseded", status="superseded")
        _insert_annotation(conn, annotation_id="ann_active", target_card_id="mem_active", status="active")
        _insert_annotation(conn, annotation_id="ann_revoked_target", target_card_id="mem_revoked", status="active")
        _insert_annotation(conn, annotation_id="ann_superseded_target", target_card_id="mem_superseded", status="active")
        _insert_annotation(conn, annotation_id="ann_dangling", target_card_id="mem_missing", status="dangling")
        _insert_annotation(conn, annotation_id="ann_revoked", target_card_id="mem_active", status="revoked")

        rows = fetch_all(
            conn,
            "select annotation_id, target_card_id, status from active_memory_annotations order by annotation_id",
        )
    finally:
        conn.close()

    assert rows == [{"annotation_id": "ann_active", "target_card_id": "mem_active", "status": "active"}]


def _insert_card(conn, *, card_id: str, status: str) -> None:
    conn.execute(
        """
        insert into memory_cards (
          card_id, owner_did, claim_type, claim, subject_json, evidence_refs_json,
          candidate_claim, visibility_json, tags_json, status, source_event_hash,
          created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            "did:key:test-owner",
            "decision",
            f"{card_id} claim",
            json.dumps({"type": "project", "id": "pcn", "label": "PCN"}, sort_keys=True),
            "[]",
            None,
            json.dumps({"type": "private"}, sort_keys=True),
            "[]",
            status,
            f"sha256:{card_id}",
            "2087-05-10T00:00:00+08:00",
            "2087-05-10T00:00:00+08:00",
        ),
    )


def _insert_annotation(conn, *, annotation_id: str, target_card_id: str, status: str) -> None:
    conn.execute(
        """
        insert into memory_annotations (
          annotation_id, target_card_id, author_did, annotation_type, body,
          status, source_event_hash, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            annotation_id,
            target_card_id,
            "did:key:commenter",
            "comment",
            f"{annotation_id} body",
            status,
            f"sha256:{annotation_id}",
            "2087-05-10T00:00:00+08:00",
        ),
    )
