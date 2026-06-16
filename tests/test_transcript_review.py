from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.transcript_review import (
    accept_remaining_segments,
    accepted_segments_clause,
    review_segment,
    reviewed_segments_for_session,
    session_review_status,
)


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
