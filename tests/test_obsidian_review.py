from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_publish_and_confirm_checked_memory_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)

    review_path = publish_candidate_review(config=config, day="2087-05-10")

    assert review_path == config.obsidian_vault / "30_Memory_Candidates" / "2087-05-10.md"
    text = review_path.read_text(encoding="utf-8")
    assert "- [ ] cand_test_001 | requirement | 用户要求音频本地处理。" in text

    review_path.write_text(text.replace("- [ ] cand_test_001", "- [x] cand_test_001"), encoding="utf-8")
    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 1
    assert result.signed_events_created == 1

    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        events = fetch_all(conn, "select event_type, trust_status from signed_events")
    finally:
        conn.close()

    assert candidates[0]["status"] == "confirmed"
    assert candidates[0]["memory_card_id"].startswith("mem_")
    assert events == [{"event_type": "memory_card.created", "trust_status": "trusted"}]


def test_confirm_review_rewrites_checked_candidates_as_read_only_receipts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(text.replace("- [ ] cand_test_001", "- [x] cand_test_001"), encoding="utf-8")

    first = confirm_checked_candidates(config=config, day="2087-05-10")

    assert first.candidates_confirmed == 1
    receipt = review_path.read_text(encoding="utf-8")
    assert "<!-- pcn:review_receipt start kind=\"managed\" candidate_id=\"cand_test_001\"" in receipt
    assert "action=confirm" in receipt
    assert "card_id=mem_" in receipt
    assert "- [x] cand_test_001" not in receipt

    review_path.write_text(receipt + "\n- [x] cand_test_001 | requirement | 用户要求音频本地处理。\n", encoding="utf-8")
    second = confirm_checked_candidates(config=config, day="2087-05-10")

    assert second.candidates_confirmed == 0
    assert second.signed_events_created == 0
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type from signed_events")
    finally:
        conn.close()
    assert events == [{"event_type": "memory_card.created"}]


def test_sync_review_rejects_candidate_without_signed_event(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(
        text.replace("- [ ] cand_test_001 | requirement | 用户要求音频本地处理。", "- [x] cand_test_001 | requirement | 用户要求音频本地处理。 | reject"),
        encoding="utf-8",
    )

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 0
    assert result.signed_events_created == 0
    receipt = review_path.read_text(encoding="utf-8")
    assert "action=reject" in receipt
    assert "card_id=" not in receipt
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        events = fetch_all(conn, "select event_type from signed_events")
    finally:
        conn.close()
    assert candidates == [{"status": "rejected", "memory_card_id": None}]
    assert events == []


def test_sync_review_defers_candidate_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(
        text.replace("- [ ] cand_test_001 | requirement | 用户要求音频本地处理。", "- [x] cand_test_001 | requirement | 用户要求音频本地处理。 | defer"),
        encoding="utf-8",
    )

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 0
    assert result.signed_events_created == 0
    receipt = review_path.read_text(encoding="utf-8")
    assert "action=defer" in receipt
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        events = fetch_all(conn, "select event_type from signed_events")
    finally:
        conn.close()
    assert candidates == [{"status": "pending_review", "memory_card_id": None}]
    assert events == []


def test_sync_review_marks_evidence_session_excluded_from_memory(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_session_with_segment(config.database_path)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(
        text.replace(
            "- [ ] cand_test_001 | requirement | 用户要求音频本地处理。",
            "- [x] cand_test_001 | requirement | 用户要求音频本地处理。 | exclude_from_memory",
        ),
        encoding="utf-8",
    )

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 0
    assert result.signed_events_created == 0
    receipt = review_path.read_text(encoding="utf-8")
    assert "action=exclude_from_memory" in receipt
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        sessions = fetch_all(conn, "select exclude_from_memory from sessions where session_id = 'ses_test'")
        events = fetch_all(conn, "select event_type from signed_events")
    finally:
        conn.close()
    assert candidates == [{"status": "excluded_from_memory", "memory_card_id": None}]
    assert sessions == [{"exclude_from_memory": 1}]
    assert events == []


def test_sync_review_edits_claim_while_preserving_candidate_claim(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    edited_claim = "用户要求 ASR 和原始转写必须在本地处理。"
    review_path.write_text(
        text.replace(
            "- [ ] cand_test_001 | requirement | 用户要求音频本地处理。",
            f"- [x] cand_test_001 | requirement | 用户要求音频本地处理。 | edit: {edited_claim}",
        ),
        encoding="utf-8",
    )

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 1
    assert result.signed_events_created == 1
    receipt = review_path.read_text(encoding="utf-8")
    assert "action=edit" in receipt
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select claim, candidate_claim from memory_cards")
    finally:
        conn.close()
    assert cards == [{"claim": edited_claim, "candidate_claim": "用户要求音频本地处理。"}]


def test_sync_review_logs_empty_edit_claim_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(
        text.replace(
            "- [ ] cand_test_001 | requirement | 用户要求音频本地处理。",
            "- [x] cand_test_001 | requirement | 用户要求音频本地处理。 | edit:",
        ),
        encoding="utf-8",
    )

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 0
    assert result.signed_events_created == 0
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        events = fetch_all(conn, "select event_type from signed_events")
        logs = fetch_all(conn, "select source, status, message from sync_logs")
    finally:
        conn.close()
    assert candidates == [{"status": "pending_review", "memory_card_id": None}]
    assert events == []
    assert logs == [{"source": "memory_candidate_review", "status": "failed", "message": "empty edit claim: cand_test_001"}]


def test_sync_review_skips_recently_modified_review_file(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        owner_did="did:key:test-owner",
        edit_grace_seconds=120,
    )
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(text.replace("- [ ] cand_test_001", "- [x] cand_test_001"), encoding="utf-8")

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 0
    assert result.signed_events_created == 0
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        events = fetch_all(conn, "select event_type from signed_events")
        logs = fetch_all(conn, "select source, status, message from sync_logs")
    finally:
        conn.close()
    assert candidates == [{"status": "pending_review", "memory_card_id": None}]
    assert events == []
    assert logs == [{"source": "memory_candidate_review", "status": "skipped", "message": "review file modified within edit grace: 2087-05-10"}]


def test_confirming_multiple_candidates_creates_owner_hash_chain(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner", edit_grace_seconds=0)
    _insert_candidate(config.database_path, candidate_id="cand_test_001", claim="用户要求音频本地处理。")
    _insert_candidate(config.database_path, candidate_id="cand_test_002", claim="用户决定保留本地证据链。")
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(text.replace("- [ ]", "- [x]"), encoding="utf-8")

    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 2
    assert result.signed_events_created == 2
    conn = connect(config.database_path)
    try:
        events = fetch_all(
            conn,
            """
            select event_hash, owner_id, owner_sequence, prev_event_hash,
                   raw_event_json, signing_body_json, canonical_signing_body_hash, trust_status
            from signed_events
            order by owner_sequence
            """,
        )
    finally:
        conn.close()

    assert events[0]["owner_id"] == "did:key:test-owner"
    assert events[0]["owner_sequence"] == 1
    assert events[0]["prev_event_hash"] is None
    assert events[0]["event_hash"] == events[0]["canonical_signing_body_hash"]
    assert events[0]["trust_status"] == "trusted"
    assert events[1]["owner_sequence"] == 2
    assert events[1]["prev_event_hash"] == events[0]["event_hash"]
    assert events[1]["event_hash"] == events[1]["canonical_signing_body_hash"]
    assert '"signature"' in events[1]["raw_event_json"]
    assert '"signature"' not in events[1]["signing_body_json"]


def _insert_candidate(
    database_path: Path,
    *,
    candidate_id: str = "cand_test_001",
    claim: str = "用户要求音频本地处理。",
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                claim,
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
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_session_with_segment(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into sessions (
              session_id, date_key, started_at, ended_at, source,
              segment_count, active_speech_ms, first_segment_id, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_test",
                "2087-05-10",
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:10:00+08:00",
                "derived_from_segments",
                1,
                1000,
                "seg_test",
                "2087-05-10T09:00:00+08:00",
                "2087-05-10T09:00:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
                "ses_test",
                0,
                1000,
                "音频必须本地处理。",
                "zh",
                "self",
                "ev_test",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.commit()
    finally:
        conn.close()
