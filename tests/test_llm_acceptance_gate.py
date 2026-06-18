from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, SessionSummary
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.session_summaries import summarize_session
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcript_review import review_segment


class RecordingLLM:
    def __init__(self) -> None:
        self.session_segments: list[dict[str, object]] = []
        self.daily_segments: list[dict[str, object]] = []

    def generate_session_summary(self, *, session_id: str, transcript_segments, prompt=None):
        self.session_segments = transcript_segments
        return SessionSummary(session_id=session_id, headline="h", summary="s", topics=[], decisions=[], todos=[], open_questions=[])

    def generate_daily_context(self, *, day: str, transcript_segments):
        self.daily_segments = transcript_segments
        return DailyContext(day=day, summary="s", todos=[], facts=[], inferences=[], memory_candidates=[])


def test_gate_off_default_sends_all_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")  # gate off
    _insert_session(config.database_path)
    llm = RecordingLLM()
    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert {s["segment_id"] for s in llm.session_segments} == {"seg_accepted", "seg_other"}


def test_gate_on_session_summary_only_accepted(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", require_accepted_transcripts=True)
    _insert_session(config.database_path)
    review_segment(config=config, segment_id="seg_accepted", status="accepted", note="")
    review_segment(config=config, segment_id="seg_other", status="rejected", note="")
    llm = RecordingLLM()
    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert [s["segment_id"] for s in llm.session_segments] == ["seg_accepted"]


def test_gate_on_daily_only_accepted(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", require_accepted_transcripts=True)
    _insert_session(config.database_path)
    review_segment(config=config, segment_id="seg_accepted", status="accepted", note="")
    llm = RecordingLLM()
    generate_daily_context(config=config, day="2087-05-10", llm=llm)
    assert [s["segment_id"] for s in llm.daily_segments] == ["seg_accepted"]


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", 2, 2000, "seg_accepted", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(["seg_accepted", "seg_other"]):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_test", f"chk_{segment_id}", "ses_test", index * 1000, (index + 1) * 1000, segment_id, "zh", "self", "self", f"ev_{index}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
