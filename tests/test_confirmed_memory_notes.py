from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_memory import publish_confirmed_memory_note
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, initialize


def test_publish_confirmed_memory_note_writes_daily_confirmed_cards(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    confirm_checked_candidates(config=config, day="2087-05-10")

    result = publish_confirmed_memory_note(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "40_Confirmed_Memory" / "2087-05-10.md"
    assert result.notes_written == 1
    assert result.note_path == note_path
    text = note_path.read_text(encoding="utf-8")
    assert text.startswith(
        "---\n"
        "pcn_schema: markdown_note.v1\n"
        "note_type: confirmed_memory\n"
        "date_key: 2087-05-10\n"
        "generated_by: personal-context-node\n"
    )
    assert "\npcn_managed: true\n---\n" in text
    assert "## Confirmed Memory" in text
    assert "用户要求音频本地处理。" in text
    assert "claim_type: requirement" in text
    assert "subject: Personal Context Node" in text
    assert "evidence: ev_test -> seg_test" in text
    assert '<!-- pcn:block start id="confirmed_memory_card:mem_' in text
    assert '<!-- pcn:block end id="confirmed_memory_card:mem_' in text
    assert "pcn:managed start" not in text


def test_publish_confirmed_memory_note_uses_candidate_date_key_without_review_path(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_confirmed_card_with_candidate_date_key(config.database_path)

    result = publish_confirmed_memory_note(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "40_Confirmed_Memory" / "2087-05-10.md"
    assert result.notes_written == 1
    assert note_path.exists()
    assert "直接按日期键发布的确认记忆。" in note_path.read_text(encoding="utf-8")


def _insert_candidate(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id, date_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cand_test_001",
                "用户要求音频本地处理。",
                "requirement",
                json.dumps({"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}),
                0.95,
                json.dumps(
                    [
                        {
                            "evidence_id": "ev_test",
                            "source_type": "transcript_segment",
                            "source_id": "seg_test",
                            "quote": "音频必须本地处理。",
                        }
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "pending_review",
                None,
                "2087-05-10",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_confirmed_card_with_candidate_date_key(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        subject_json = json.dumps(
            {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"},
            ensure_ascii=False,
            sort_keys=True,
        )
        evidence_refs_json = json.dumps(
            [
                {
                    "evidence_id": "ev_direct",
                    "source_type": "transcript_segment",
                    "source_id": "seg_direct",
                    "quote": "直接按日期键发布的确认记忆。",
                }
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        conn.execute(
            """
            insert into memory_cards (
              card_id, current_version, owner_id, owner_did, claim_type, claim,
              source_type, confidence, subject_json, evidence_refs_json,
              visibility_json, tags_json, status, source_event_hash, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mem_direct_date_key",
                1,
                "did:key:test-owner",
                "did:key:test-owner",
                "requirement",
                "直接按日期键发布的确认记忆。",
                "confirmed_generated",
                0.95,
                subject_json,
                evidence_refs_json,
                json.dumps({"type": "private"}, sort_keys=True),
                "[]",
                "active",
                "sha256:direct",
                "2087-05-11T00:00:00+08:00",
                "2087-05-11T00:00:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id,
              created_card_id, date_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cand_direct_date_key",
                "直接按日期键发布的确认记忆。",
                "requirement",
                subject_json,
                0.95,
                evidence_refs_json,
                "confirmed",
                "mem_direct_date_key",
                "mem_direct_date_key",
                "2087-05-10",
            ),
        )
        conn.commit()
    finally:
        conn.close()
