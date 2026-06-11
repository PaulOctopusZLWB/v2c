from __future__ import annotations

import json
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


class EvidenceIdLLM:
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, str]]) -> DailyContext:
        return DailyContext(
            day=day,
            summary="summary",
            todos=[],
            facts=[],
            inferences=[],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim="用户要求音频和转写处理保持本地。",
                    claim_type="requirement",
                    confidence=0.91,
                    evidence_source_ids=[transcript_segments[0]["evidence_id"]],
                )
            ],
        )


class UnknownEvidenceLLM:
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, str]]) -> DailyContext:
        return DailyContext(
            day=day,
            summary="summary",
            todos=[],
            facts=[],
            inferences=[],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim="用户要求音频和转写处理保持本地。",
                    claim_type="requirement",
                    confidence=0.91,
                    evidence_source_ids=["ev_missing"],
                )
            ],
        )


class DuplicateDailyCandidateLLM:
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, str]]) -> DailyContext:
        return DailyContext(
            day=day,
            summary="summary",
            todos=[],
            facts=[],
            inferences=[],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim="用户要求音频和转写处理保持本地。",
                    claim_type="requirement",
                    confidence=0.7,
                    evidence_source_ids=[transcript_segments[0]["evidence_id"]],
                ),
                MemoryCandidateDraft(
                    candidate_claim=" 用户要求音频和转写处理保持本地。 ",
                    claim_type="requirement",
                    confidence=0.9,
                    evidence_source_ids=[transcript_segments[1]["evidence_id"]],
                ),
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
        formal_summaries = fetch_all(
            conn,
            """
            select summary_type, target_type, target_id, prompt_version, content_json
            from summaries
            where summary_type = 'daily'
            """,
        )
        candidates = fetch_all(conn, "select candidate_claim, claim_type, evidence_refs_json, status from memory_candidates")
        evidence_refs = fetch_all(
            conn,
            "select evidence_id, source_type, source_id, quote from evidence_refs",
        )
    finally:
        conn.close()

    assert summaries[0]["day"] == "2087-05-10"
    assert "本地上下文系统" in summaries[0]["summary"]
    assert formal_summaries[0]["target_type"] == "date_key"
    assert formal_summaries[0]["target_id"] == "2087-05-10"
    assert formal_summaries[0]["prompt_version"] == "llm_port.daily_summary.v1"
    content = json.loads(str(formal_summaries[0]["content_json"]))
    assert content["schema_version"] == "daily_summary.v1"
    assert content["date_key"] == "2087-05-10"
    assert content["headline"] == "今天讨论了本地上下文系统。"
    assert content["todos_rollup"] == [
        {"text": "继续接入真实 ASR", "owner": "self", "session_id": None, "evidence_refs": ["ev_test"]}
    ]
    assert candidates[0]["claim_type"] == "requirement"
    assert candidates[0]["status"] == "pending_review"
    assert "ev_test" in candidates[0]["evidence_refs_json"]
    assert evidence_refs == [
        {
            "evidence_id": "ev_test",
            "source_type": "transcript_segment",
            "source_id": "seg_test",
            "quote": "我要求音频和转写处理保持本地。",
        }
    ]


def test_generate_daily_context_accepts_llm_evidence_id_refs(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_transcript(config.database_path)

    result = generate_daily_context(config=config, day="2087-05-10", llm=EvidenceIdLLM())

    assert result.memory_candidates_created == 1
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select evidence_refs_json from memory_candidates")
    finally:
        conn.close()
    assert json.loads(candidates[0]["evidence_refs_json"]) == [
        {
            "evidence_id": "ev_test",
            "source_type": "transcript_segment",
            "source_id": "seg_test",
            "quote": "我要求音频和转写处理保持本地。",
        }
    ]


def test_generate_daily_context_rejects_unknown_llm_evidence_refs_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_transcript(config.database_path)

    try:
        generate_daily_context(config=config, day="2087-05-10", llm=UnknownEvidenceLLM())
    except ValueError as exc:
        assert "unknown evidence_id: ev_missing" in str(exc)
    else:
        raise AssertionError("unknown LLM evidence reference was accepted")

    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select candidate_id from memory_candidates")
        summaries = fetch_all(conn, "select summary_id from summaries")
        legacy_summaries = fetch_all(conn, "select day from daily_summaries")
    finally:
        conn.close()
    assert candidates == []
    assert summaries == []
    assert legacy_summaries == []


def test_generate_daily_context_merges_duplicate_candidates_within_day(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_transcript(config.database_path)
    _insert_transcript_segment(
        config.database_path,
        segment_id="seg_test_002",
        evidence_id="ev_test_002",
        text="我再次要求音频和转写处理保持本地。",
    )

    result = generate_daily_context(config=config, day="2087-05-10", llm=DuplicateDailyCandidateLLM())

    assert result.memory_candidates_created == 1
    conn = connect(config.database_path)
    try:
        candidates = fetch_all(
            conn,
            "select candidate_claim, claim_type, confidence, evidence_refs_json, status from memory_candidates",
        )
    finally:
        conn.close()
    assert len(candidates) == 1
    assert candidates[0]["candidate_claim"] == "用户要求音频和转写处理保持本地。"
    assert candidates[0]["claim_type"] == "requirement"
    assert candidates[0]["confidence"] == 0.9
    assert candidates[0]["status"] == "pending_review"
    assert json.loads(candidates[0]["evidence_refs_json"]) == [
        {
            "evidence_id": "ev_test",
            "source_type": "transcript_segment",
            "source_id": "seg_test",
            "quote": "我要求音频和转写处理保持本地。",
        },
        {
            "evidence_id": "ev_test_002",
            "source_type": "transcript_segment",
            "source_id": "seg_test_002",
            "quote": "我再次要求音频和转写处理保持本地。",
        },
    ]


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


def _insert_transcript_segment(database_path: Path, *, segment_id: str, evidence_id: str, text: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment_id,
                "aud_test",
                f"chk_{segment_id}",
                1000,
                2000,
                text,
                "zh",
                "self",
                evidence_id,
                0.98,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.commit()
    finally:
        conn.close()
