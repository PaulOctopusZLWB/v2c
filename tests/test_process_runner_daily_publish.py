from __future__ import annotations

from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.errors import TerminalPortError
from personal_context_node.core.ports.llm import DailyContext, MemoryCandidateDraft, SessionSummary
import personal_context_node.process_runner as process_runner
from personal_context_node.process_runner import PipelineEdge, process_once
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task, process_status_rows


class RecordingLLM:
    def __init__(self) -> None:
        self.daily_segments: list[dict[str, object]] = []

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        self.daily_segments = transcript_segments
        return DailyContext(
            day=day,
            summary="模拟 LLM 汇总：无需外部 API。",
            todos=[],
            facts=[],
            inferences=[],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim="模拟 LLM 认为音频处理必须保持本地。",
                    claim_type="requirement",
                    confidence=0.88,
                    evidence_source_ids=[str(transcript_segments[0]["evidence_id"])],
                )
            ],
        )

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        raise AssertionError("daily_generate should not request a session summary")


class TerminalLLM:
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        raise TerminalPortError("invalid LLM contract")

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        raise AssertionError("daily_generate should not request a session summary")


def test_process_runner_generates_daily_and_publishes_obsidian(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_transcript(config.database_path)
    enqueue_task(config=config, task_type="daily_generate", target_type="date_key", target_id="2087-05-10")

    daily = process_once(
        config=config,
        run_id="run_daily",
        vad=EnergyVadAdapter(),
        asr=MockASRAdapter(),
    )

    assert daily.task_type == "daily_generate"
    assert daily.status == "succeeded"
    assert any(
        row["task_type"] == "obsidian_publish"
        and row["target_id"] == "2087-05-10"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )

    publish = process_once(
        config=config,
        run_id="run_publish",
        vad=EnergyVadAdapter(),
        asr=MockASRAdapter(),
    )

    assert publish.task_type == "obsidian_publish"
    assert publish.status == "succeeded"
    daily_note = config.obsidian_vault / "10_Daily" / "2087-05-10.md"
    assert daily_note.exists()
    daily_text = daily_note.read_text(encoding="utf-8")
    assert "source_run_id: run_publish\n" in daily_text
    assert "<!-- pcn:managed start type=\"daily_headline\" date_key=\"2087-05-10\" -->" in daily_text
    assert "<!-- pcn:managed start type=\"daily_sessions\" date_key=\"2087-05-10\" -->" in daily_text
    assert "- [[20_Conversations/2087-05-10/ses_test|ses_test]]" in daily_text
    assert "<!-- pcn:managed start type=\"daily_decisions\" date_key=\"2087-05-10\" -->" in daily_text
    assert (config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md").exists()
    review_note = config.obsidian_vault / "30_Memory_Candidates" / "2087-05-10.md"
    assert review_note.exists()
    assert "source_run_id: run_publish\n" in review_note.read_text(encoding="utf-8")
    assert (config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md").exists()


def test_process_once_daily_generate_uses_injected_llm_adapter(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_transcript(config.database_path)
    enqueue_task(config=config, task_type="daily_generate", target_type="date_key", target_id="2087-05-10")
    llm = RecordingLLM()

    result = process_once(
        config=config,
        run_id="run_daily_fake_llm",
        vad=EnergyVadAdapter(),
        asr=MockASRAdapter(),
        llm=llm,
    )

    assert result.task_type == "daily_generate"
    assert result.status == "succeeded"
    assert llm.daily_segments == [
        {
            "segment_id": "seg_test",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "我决定继续接入真实 ASR，需要保持音频本地处理。",
            "evidence_id": "ev_test",
            "speaker": "self",
        }
    ]
    assert "wav" not in str(llm.daily_segments).lower()

    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select candidate_claim, source_type, status from memory_candidates")
    finally:
        conn.close()

    assert candidates == [
        {
            "candidate_claim": "模拟 LLM 认为音频处理必须保持本地。",
            "source_type": "llm_daily_context",
            "status": "pending_review",
        }
    ]


def test_process_once_rolls_back_success_when_downstream_registration_fails(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_transcript(config.database_path)
    task = enqueue_task(config=config, task_type="daily_generate", target_type="date_key", target_id="2087-05-10")
    llm = RecordingLLM()

    monkeypatch.setattr(
        process_runner,
        "PIPELINE",
        (
            PipelineEdge("daily_generate", "obsidian_publish", "date_key", lambda _conn, _config, target_id: [target_id]),
            PipelineEdge("daily_generate", None, "date_key", lambda _conn, _config, target_id: [target_id]),
        ),
    )

    try:
        process_once(
            config=config,
            run_id="run_daily_atomic",
            vad=EnergyVadAdapter(),
            asr=MockASRAdapter(),
            llm=llm,
        )
    except ValueError as exc:
        assert "unknown task_type" in str(exc)
    else:
        raise AssertionError("process_once should surface downstream registration failure")

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select status from tasks where task_id = ?", (task.task_id,))
        publish_tasks = fetch_all(conn, "select task_id from tasks where task_type = 'obsidian_publish'")
    finally:
        conn.close()

    assert rows == [{"status": "failed_retryable"}]
    assert publish_tasks == []


def test_process_once_marks_terminal_port_errors_terminal(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_transcript(config.database_path)
    task = enqueue_task(config=config, task_type="daily_generate", target_type="date_key", target_id="2087-05-10")

    try:
        process_once(
            config=config,
            run_id="run_daily_terminal",
            vad=EnergyVadAdapter(),
            asr=MockASRAdapter(),
            llm=TerminalLLM(),
        )
    except TerminalPortError:
        pass
    else:
        raise AssertionError("process_once should surface terminal port errors")

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select status, last_error from tasks where task_id = ?", (task.task_id,))
    finally:
        conn.close()

    assert rows == [{"status": "failed_terminal", "last_error": "invalid LLM contract"}]


def _insert_session_and_transcript(database_path: Path) -> None:
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
                "我决定继续接入真实 ASR，需要保持音频本地处理。",
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
