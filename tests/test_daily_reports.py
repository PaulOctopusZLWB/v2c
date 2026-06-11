from __future__ import annotations

import json
import os
import time
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, MemoryCandidateDraft
from personal_context_node.daily_reports import get_daily_report_status, set_daily_report_status
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, initialize


class RecordingLLM:
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, str]]) -> DailyContext:
        return DailyContext(
            day=day,
            summary="summary",
            todos=[],
            facts=[],
            inferences=[],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim="用户要求音频本地处理。",
                    claim_type="requirement",
                    confidence=0.9,
                    evidence_source_ids=[transcript_segments[0]["evidence_id"]],
                )
            ],
        )


def test_daily_report_status_moves_generated_to_review_synced(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_transcript(config.database_path)

    generate_daily_context(config=config, day="2087-05-10", llm=RecordingLLM())

    assert get_daily_report_status(config=config, day="2087-05-10") == "generated"

    review_path = publish_candidate_review(config=config, day="2087-05-10")

    assert get_daily_report_status(config=config, day="2087-05-10") == "review_pending"

    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    assert get_daily_report_status(config=config, day="2087-05-10") == "review_synced"


def test_daily_report_status_updates_legacy_day_table_by_date_key(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        conn.execute(
            """
            create table daily_reports (
              day text primary key,
              status text not null,
              updated_at text not null,
              error text
            )
            """
        )
        conn.execute(
            "insert into daily_reports (day, status, updated_at, error) values (?, ?, ?, ?)",
            ("2087-05-10", "generated", "2087-05-10T00:00:00Z", None),
        )
        conn.commit()
    finally:
        conn.close()

    set_daily_report_status(config=config, day="2087-05-10", status="review_pending")

    assert get_daily_report_status(config=config, day="2087-05-10") == "review_pending"


def _mark_review_stable(path: Path) -> None:
    stable_time = time.time() - 121
    os.utime(path, (stable_time, stable_time))


def _insert_transcript(database_path: Path) -> None:
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
              segment_count, active_speech_ms, first_segment_id,
              exclude_from_memory, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_test",
                "2087-05-10",
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:00:01+08:00",
                "derived_from_segments",
                1,
                1000,
                "seg_test",
                0,
                "2087-05-10T00:10:00+08:00",
                "2087-05-10T00:10:00+08:00",
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
                "我要求音频和转写处理保持本地。",
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
