from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.transcript_review import (
    accept_remaining_segments,
    accepted_segments_clause,
    batch_review_segments,
    clear_review_segments,
    review_queue,
    review_segment,
    reviewed_segments_for_session,
    search_transcripts,
    session_review_status,
)

import pytest


def test_config_defaults_gate_off() -> None:
    assert AppConfig().require_accepted_transcripts is False


def test_accepted_segments_clause_is_a_correlated_exists() -> None:
    clause = accepted_segments_clause("ts")
    assert "transcript_segment_reviews" in clause
    assert "ts.segment_id" in clause
    assert "accepted" in clause


def test_review_segment_persists_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)

    review_segment(config=config, segment_id="seg_1", status="accepted", note="")

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select segment_id, status, reviewer, note from transcript_segment_reviews")
    finally:
        conn.close()
    assert rows == [{"segment_id": "seg_1", "status": "accepted", "reviewer": "local_user", "note": ""}]


def test_session_review_status_blocks_on_needs_fix(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="accepted", note="")
    review_segment(config=config, segment_id="seg_2", status="needs_fix", note="听不清")
    assert session_review_status(config=config, session_id="ses_test") == "blocked"


def test_accept_remaining_accepts_only_pending(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="rejected", note="噪音")
    assert accept_remaining_segments(config=config, session_id="ses_test") == {"accepted": 1}
    rows = reviewed_segments_for_session(config=config, session_id="ses_test")
    assert [(r["segment_id"], r["review_status"]) for r in rows] == [("seg_1", "rejected"), ("seg_2", "accepted")]


def test_batch_review_segments_one_upsert(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)

    assert batch_review_segments(config=config, segment_ids=["seg_1", "seg_2"], status="accepted") == 2

    rows = reviewed_segments_for_session(config=config, session_id="ses_test")
    assert [(r["segment_id"], r["review_status"]) for r in rows] == [("seg_1", "accepted"), ("seg_2", "accepted")]


def test_batch_review_rejects_pending_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)

    with pytest.raises(ValueError):
        batch_review_segments(config=config, segment_ids=["seg_1"], status="pending_review")
    with pytest.raises(ValueError):
        batch_review_segments(config=config, segment_ids=["seg_1"], status="bogus")


def test_batch_review_empty_is_noop(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)

    assert batch_review_segments(config=config, segment_ids=[], status="accepted") == 0

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select segment_id from transcript_segment_reviews")
    finally:
        conn.close()
    assert rows == []


def test_accept_remaining_still_accepts_only_pending(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="rejected", note="噪音")

    assert accept_remaining_segments(config=config, session_id="ses_test") == {"accepted": 1}

    rows = reviewed_segments_for_session(config=config, session_id="ses_test")
    assert [(r["segment_id"], r["review_status"]) for r in rows] == [("seg_1", "rejected"), ("seg_2", "accepted")]


def test_clear_review_reverts_to_pending(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="accepted", note="")

    assert clear_review_segments(config=config, segment_ids=["seg_1"]) == 1

    rows = reviewed_segments_for_session(config=config, session_id="ses_test")
    assert [(r["segment_id"], r["review_status"]) for r in rows] == [
        ("seg_1", "pending_review"),
        ("seg_2", "pending_review"),
    ]
    # The review row is gone (not just status-flipped — there is no 'pending_review' row).
    conn = connect(config.database_path)
    try:
        remaining = fetch_all(conn, "select segment_id from transcript_segment_reviews")
    finally:
        conn.close()
    assert remaining == []


def test_clear_review_empty_is_noop(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    assert clear_review_segments(config=config, segment_ids=[]) == 0


def test_clear_review_counts_only_deleted_rows(tmp_path: Path) -> None:
    # Only seg_1 has a review row; clearing [seg_1, seg_2] deletes one row -> count 1.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="rejected", note="")
    assert clear_review_segments(config=config, segment_ids=["seg_1", "seg_2"]) == 1


def test_clear_review_chunks_large_input(tmp_path: Path) -> None:
    # >999 ids in a single DELETE would trip SQLite's per-statement variable limit; chunk it.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    ids = [f"seg_{i:04d}" for i in range(1200)]
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_big", "DJI Mic 3", "/source/big.wav", 1, 1, "/raw/big.wav", "sha256:big", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_big", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", len(ids), 2000, ids[0], "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(ids):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_big", f"chk_{segment_id}", "ses_big", index, index + 1, "t", "zh", "self", "self", f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
    batch_review_segments(config=config, segment_ids=ids, status="accepted")

    assert clear_review_segments(config=config, segment_ids=ids) == 1200

    conn = connect(config.database_path)
    try:
        remaining = fetch_all(conn, "select segment_id from transcript_segment_reviews")
    finally:
        conn.close()
    assert remaining == []


def test_segments_ordered_by_absolute_timeline_across_files(tmp_path: Path) -> None:
    # A whole-day session fans in multiple files; per-file start_ms must NOT decide order.
    # seg_a has the LOWER start_ms but the LATER wall-clock; seg_b is the reverse. The result
    # must follow absolute_start_at (seg_b first), and expose the absolute fields to the UI.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for aud in ("aud_a", "aud_b"):
            conn.execute(
                "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aud, "DJI Mic 3", f"/source/{aud}.wav", 1, 1, f"/raw/{aud}.wav", f"sha256:{aud}", 600000, "2026-06-13T09:00:00+08:00", "2026-06-13T09:00:00+08:00", "imported"),
            )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_multi", "2026-06-13", "2026-06-13T09:00:00+08:00", "2026-06-13T09:01:00+08:00", "derived_from_segments", 2, 2000, "seg_b", "2026-06-13T09:02:00+08:00", "2026-06-13T09:02:00+08:00"),
        )
        # (segment_id, audio_file_id, start_ms, end_ms, absolute_start_at, absolute_end_at)
        seg_rows = [
            ("seg_a", "aud_a", 100, 1100, "2026-06-13T09:00:30.000000+08:00", "2026-06-13T09:00:31.000000+08:00"),
            ("seg_b", "aud_b", 5000, 6000, "2026-06-13T09:00:05.000000+08:00", "2026-06-13T09:00:06.000000+08:00"),
        ]
        for sid, aud, start_ms, end_ms, abs_start, abs_end in seg_rows:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, aud, f"diar_{aud}_{start_ms:09d}", "ses_multi", start_ms, end_ms, abs_start, abs_end, sid, "zh", "spk_01", "spk_01", f"ev_{sid}", 1.0, "FunASRParaformerDiarize", "paraformer-zh", "test", 1, "2026-06-13T09:02:00+08:00"),
            )
        conn.commit()
    finally:
        conn.close()

    rows = reviewed_segments_for_session(config=config, session_id="ses_multi")

    assert [r["segment_id"] for r in rows] == ["seg_b", "seg_a"]  # absolute timeline, not start_ms
    assert rows[0]["absolute_start_at"] == "2026-06-13T09:00:05.000000+08:00"
    assert rows[0]["absolute_end_at"] == "2026-06-13T09:00:06.000000+08:00"


def test_batch_review_chunks_large_input(tmp_path: Path) -> None:
    # >999 segment ids (6 bind vars each) must not trip SQLite's per-statement variable limit.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    ids = [f"seg_{i:04d}" for i in range(1200)]
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_big", "DJI Mic 3", "/source/big.wav", 1, 1, "/raw/big.wav", "sha256:big", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_big", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", len(ids), 2000, ids[0], "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(ids):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_big", f"chk_{segment_id}", "ses_big", index, index + 1, "t", "zh", "self", "self", f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()

    assert batch_review_segments(config=config, segment_ids=ids, status="accepted") == 1200
    rows = reviewed_segments_for_session(config=config, session_id="ses_big")
    assert sum(1 for r in rows if r["review_status"] == "accepted") == 1200


def test_search_transcripts_matches_substring(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_texts(
        config.database_path,
        [("seg_1", "数据不出本机"), ("seg_2", "继续完善系统"), ("seg_3", "天气不错")],
    )

    results = search_transcripts(config=config, query="数据")

    assert len(results) == 1
    hit = results[0]
    assert hit["segment_id"] == "seg_1"
    assert hit["session_id"] == "ses_text"
    assert hit["day"] == "2087-05-10"
    assert hit["speaker"] == "self"
    assert hit["text"] == "数据不出本机"
    assert hit["absolute_start_at"] == "2087-05-10T08:00:00.000000+08:00"


def test_search_transcripts_orders_by_absolute_start_desc_and_limits(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_texts(
        config.database_path,
        [("seg_1", "完善 A"), ("seg_2", "完善 B"), ("seg_3", "完善 C")],
    )

    results = search_transcripts(config=config, query="完善")
    # absolute_start_at is set 0,1,2 minutes apart by the helper -> newest first.
    assert [r["segment_id"] for r in results] == ["seg_3", "seg_2", "seg_1"]

    limited = search_transcripts(config=config, query="完善", limit=2)
    assert [r["segment_id"] for r in limited] == ["seg_3", "seg_2"]


def test_search_transcripts_treats_wildcards_literally(tmp_path: Path) -> None:
    # A LIKE wildcard in the user query must be escaped, so "%" matches only a literal "%".
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_texts(
        config.database_path,
        [("seg_1", "数据不出本机"), ("seg_2", "命中率 95% 达标")],
    )

    assert [r["segment_id"] for r in search_transcripts(config=config, query="%")] == ["seg_2"]
    # An underscore is likewise literal: matches nothing here, not "any single char".
    assert search_transcripts(config=config, query="_") == []


def test_search_transcripts_ignores_inactive_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_texts(
        config.database_path,
        [("seg_1", "数据保留"), ("seg_2", "数据作废")],
        inactive={"seg_2"},
    )

    assert [r["segment_id"] for r in search_transcripts(config=config, query="数据")] == ["seg_1"]


def test_search_transcripts_empty_query_returns_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_texts(config.database_path, [("seg_1", "数据不出本机")])

    assert search_transcripts(config=config, query="") == []
    assert search_transcripts(config=config, query="   ") == []


def test_review_queue_surfaces_sessions_with_pending_segments(tmp_path: Path) -> None:
    # A 2-segment session with no review rows -> one queue row, pending=2, total=2, speakers=1.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)

    queue = review_queue(config=config)

    assert len(queue) == 1
    item = queue[0]
    assert item["session_id"] == "ses_test"
    assert item["day"] == "2087-05-10"
    assert item["started_at"] == "2087-05-10T08:00:00+08:00"
    assert item["pending"] == 2
    assert item["total"] == 2
    assert item["speakers"] == 1
    assert item["has_flag"] == 0


def test_review_queue_counts_only_unreviewed_as_pending(tmp_path: Path) -> None:
    # Accept one of two segments -> still in the queue, pending drops to 1.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="accepted", note="")

    queue = review_queue(config=config)

    assert len(queue) == 1
    assert queue[0]["pending"] == 1
    assert queue[0]["total"] == 2


def test_review_queue_drops_fully_reviewed_session(tmp_path: Path) -> None:
    # Accept every segment -> the session leaves the queue (having pending > 0 fails).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    accept_remaining_segments(config=config, session_id="ses_test")

    assert review_queue(config=config) == []


def test_review_queue_ranks_flagged_session_first(tmp_path: Path) -> None:
    # Two sessions both with pending segments; the one carrying a needs_fix flag ranks first.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_two_sessions(config.database_path)
    # Flag a segment in the EARLIER session so order can't be explained by started_at alone.
    review_segment(config=config, segment_id="a_seg_1", status="needs_fix", note="听不清")

    queue = review_queue(config=config)

    assert [item["session_id"] for item in queue] == ["ses_a", "ses_b"]
    assert queue[0]["has_flag"] == 1
    # The flagged segment still has no... it HAS a review row, so it isn't pending: pending counts
    # only un-reviewed segments. ses_a has seg_2 pending; ses_b has both pending.
    assert queue[0]["session_id"] == "ses_a"
    assert queue[0]["pending"] == 1
    assert queue[1]["session_id"] == "ses_b"
    assert queue[1]["has_flag"] == 0
    assert queue[1]["pending"] == 2


def test_review_queue_respects_limit(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_two_sessions(config.database_path)

    assert len(review_queue(config=config, limit=1)) == 1


def test_review_queue_ignores_inactive_segments(tmp_path: Path) -> None:
    # An inactive (superseded) segment must not count toward pending/total.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_texts(
        config.database_path,
        [("seg_1", "保留"), ("seg_2", "作废")],
        inactive={"seg_2"},
    )

    queue = review_queue(config=config)

    assert len(queue) == 1
    assert queue[0]["session_id"] == "ses_text"
    assert queue[0]["pending"] == 1
    assert queue[0]["total"] == 1


def _insert_two_sessions(database_path: Path) -> None:
    """Two sessions on the same day: ses_a (started 08:00, 2 segs) then ses_b (09:00, 2 segs)."""
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_two", "DJI Mic 3", "/source/two.wav", 1, 1, "/raw/two.wav", "sha256:two", 600000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        sessions = [
            ("ses_a", "08:00:00", "a_seg_1"),
            ("ses_b", "09:00:00", "b_seg_1"),
        ]
        for session_id, hms, first in sessions:
            conn.execute(
                "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, "2087-05-10", f"2087-05-10T{hms}+08:00", f"2087-05-10T{hms}+08:00", "derived_from_segments", 2, 2000, first, "2087-05-10T10:00:00+08:00", "2087-05-10T10:00:00+08:00"),
            )
        seg_specs = [
            ("a_seg_1", "ses_a"), ("a_seg_2", "ses_a"),
            ("b_seg_1", "ses_b"), ("b_seg_2", "ses_b"),
        ]
        for index, (segment_id, session_id) in enumerate(seg_specs):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_two", f"chk_{segment_id}", session_id, index * 1000, (index + 1) * 1000, "t", "zh", "self", "self", f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T10:00:01+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_session_with_texts(
    database_path: Path,
    rows: list[tuple[str, str]],
    *,
    inactive: set[str] | None = None,
) -> None:
    """Insert one session ('ses_text', day 2087-05-10) with the given (segment_id, text) rows.

    Each row's absolute_start_at is its index minutes after 08:00 so search ordering is stable.
    """
    inactive = inactive or set()
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_text", "DJI Mic 3", "/source/text.wav", 1, 1, "/raw/text.wav", "sha256:text", 600000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_text", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:10:00+08:00", "derived_from_segments", len(rows), 2000, rows[0][0], "2087-05-10T08:11:00+08:00", "2087-05-10T08:11:00+08:00"),
        )
        for index, (segment_id, text) in enumerate(rows):
            abs_start = f"2087-05-10T08:0{index}:00.000000+08:00"
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_text", f"chk_{segment_id}", "ses_text", index * 1000, (index + 1) * 1000, abs_start, abs_start, text, "zh", "self", "self", f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 0 if segment_id in inactive else 1, "2087-05-10T08:11:00+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_session_with_segments(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", 2, 2000, "seg_1", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(["seg_1", "seg_2"]):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_test", f"chk_{segment_id}", "ses_test", index * 1000, (index + 1) * 1000, f"text {index + 1}", "zh", "self", "self", f"ev_{index + 1}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
