from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_active_memory_cards_view_excludes_revoked_and_superseded_cards(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_card(conn, card_id="mem_active", status="active")
        _insert_card(conn, card_id="mem_revoked", status="revoked")
        _insert_card(conn, card_id="mem_superseded", status="superseded")
        rows = fetch_all(conn, "select card_id, status from active_memory_cards order by card_id")
    finally:
        conn.close()

    assert rows == [{"card_id": "mem_active", "status": "active"}]


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
