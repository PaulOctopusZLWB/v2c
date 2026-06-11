from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, LLMPort, MemoryCandidateDraft
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


class RecordingLLM:
    def __init__(self) -> None:
        self.received_segments: list[dict[str, str]] = []

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, str]]) -> DailyContext:
        self.received_segments = transcript_segments
        return DailyContext(
            day=day,
            summary="今天讨论了本地上下文系统。",
            todos=["继续接入真实 ASR"],
            facts=["系统需要保持音频本地处理"],
            inferences=["用户关注可追溯证据链"],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim="用户要求音频和转写处理保持本地。",
                    claim_type="requirement",
                    confidence=0.91,
                    evidence_source_ids=[transcript_segments[0]["segment_id"]],
                )
            ],
        )


def test_generate_daily_context_sends_text_only_and_persists_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
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
                "/Volumes/DJI/TX02_MIC001_20870510_173550_orig.wav",
                "/private/raw/TX02_MIC001_20870510_173550_orig.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
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

    llm = RecordingLLM()
    result = generate_daily_context(config=config, day="2087-05-10", llm=llm)

    assert result.summaries_created == 1
    assert result.memory_candidates_created == 1
    assert llm.received_segments == [
        {
            "segment_id": "seg_test",
            "speaker": "self",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "我要求音频和转写处理保持本地。",
            "evidence_id": "ev_test",
        }
    ]
    assert "wav" not in str(llm.received_segments).lower()

    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select day, summary, todos_json, facts_json, inferences_json from daily_summaries")
        candidates = fetch_all(conn, "select candidate_claim, claim_type, evidence_refs_json, status from memory_candidates")
    finally:
        conn.close()

    assert summaries[0]["day"] == "2087-05-10"
    assert "本地上下文系统" in summaries[0]["summary"]
    assert candidates[0]["claim_type"] == "requirement"
    assert candidates[0]["status"] == "pending_review"
    assert "ev_test" in candidates[0]["evidence_refs_json"]
