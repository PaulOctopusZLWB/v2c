from __future__ import annotations

import math
import wave
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, SessionSummary
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import _ready_session_derive_dates, process_once
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task, process_status_rows


class RecordingSessionLLM:
    def __init__(self) -> None:
        self.session_segments: list[dict[str, object]] = []

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        raise AssertionError("summarize_session should not request daily context")

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None) -> SessionSummary:
        self.session_segments = transcript_segments
        return SessionSummary(
            session_id=session_id,
            headline="模拟 LLM session headline",
            summary="模拟 LLM session summary",
            topics=["本地处理"],
            decisions=[],
            todos=[],
            open_questions=[],
        )


def test_asr_success_enqueues_session_derive_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    # auto-chain ON: this regression covers session_derive auto-enqueuing summarize_session.
    config = AppConfig(
        data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", pipeline_auto_viewpoints=True
    )
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )
    process_once(
        config=config,
        run_id="run_asr",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    session_tasks = [
        row for row in process_status_rows(config=config)
        if row["task_type"] == "session_derive"
    ]
    assert len(session_tasks) == 1
    assert session_tasks[0]["target_type"] == "date_key"
    assert session_tasks[0]["target_id"] == "2025-06-10"
    assert session_tasks[0]["status"] == "pending"

    session_result = process_once(
        config=config,
        run_id="run_session",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert session_result.task_type == "session_derive"
    assert session_result.status == "succeeded"
    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select date_key, segment_count from sessions")
    finally:
        conn.close()
    assert sessions == [{"date_key": "2025-06-10", "segment_count": 1}]
    assert any(
        row["task_type"] == "summarize_session"
        and row["target_type"] == "session"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )


def test_session_derive_fan_in_waits_for_all_same_day_files(tmp_path: Path) -> None:
    # session_derive (and everything downstream) rebuilds the WHOLE day, so its asr fan-in must
    # wait until every recording on that day has finished ASR — not just the chunk's own file.
    # Otherwise the first recording to finish on a multi-recording day triggers a premature,
    # partial derive+publish, then a redundant re-derive/re-publish when the rest transcribe.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        # Two recordings on the SAME day (different HHMMSS — the normal case).
        for aud, recorded_at in [("aud_1", "2026-06-14T09:00:00+08:00"), ("aud_2", "2026-06-14T15:00:00+08:00")]:
            conn.execute(
                "insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256,"
                " duration_ms, recorded_at, imported_at, status) values (?, 'dev', ?, ?, ?, 1000, ?, ?, 'imported')",
                (aud, f"/{aud}.wav", f"/l_{aud}.wav", f"sha:{aud}", recorded_at, recorded_at),
            )

        def _chunk(chunk_id: str, aud: str, status: str) -> None:
            conn.execute(
                "insert into audio_chunks (chunk_id, audio_file_id, source_start_ms, source_end_ms,"
                " local_chunk_path, status) values (?, ?, 0, 1000, ?, ?)",
                (chunk_id, aud, f"/{chunk_id}.wav", status),
            )

        _chunk("chk_1", "aud_1", "transcribed")   # first recording fully transcribed
        _chunk("chk_2", "aud_2", "pending")         # second recording still pending ASR
        conn.commit()
    finally:
        conn.close()

    # aud_1 is done, but aud_2 (same day) is not -> the day is NOT ready for session_derive.
    assert _ready_session_derive_dates(config=config, chunk_id="chk_1") == []

    # Finish the second recording -> now the whole day is ready.
    conn = connect(config.database_path)
    try:
        conn.execute("update audio_chunks set status = 'transcribed' where chunk_id = 'chk_2'")
        conn.commit()
    finally:
        conn.close()
    assert _ready_session_derive_dates(config=config, chunk_id="chk_1") == ["2026-06-14"]


def test_downstream_enqueue_carries_upstream_priority_forward(tmp_path: Path) -> None:
    # ingest stamps the recording-day priority ONLY on the vad task; every later stage inherits it
    # solely via _enqueue_downstream_tasks_in_conn carrying the upstream task's priority forward.
    # Since claim_next_task now orders by priority first, losing this carry-forward would silently
    # collapse date-major scheduling (all downstream tasks revert to the flat default 100).
    from personal_context_node.process_runner import _enqueue_downstream_tasks_in_conn

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    at = "2026-06-14T09:00:00+08:00"
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256,"
            " duration_ms, recorded_at, imported_at, status) values ('aud_1','dev','/a.wav','/l.wav','sha',1000,?,?,'imported')",
            (at, at),
        )
        conn.execute(
            "insert into audio_chunks (chunk_id, audio_file_id, source_start_ms, source_end_ms, local_chunk_path, status)"
            " values ('chk_1','aud_1',0,1000,'/c.wav','transcribed')"
        )
        # The upstream asr task carries a distinctive, non-default recording-day priority.
        conn.execute(
            "insert into tasks (task_id, task_type, target_type, target_id, status, priority, available_at, created_at,"
            " updated_at) values ('t_asr','asr','audio_chunk','chk_1','succeeded',9999,?,?,?)",
            (at, at, at),
        )
        conn.commit()

        _enqueue_downstream_tasks_in_conn(conn, config=config, upstream_task_type="asr", upstream_target_id="chk_1")
        conn.commit()
        rows = fetch_all(conn, "select priority from tasks where task_type = 'session_derive'")
    finally:
        conn.close()

    assert [r["priority"] for r in rows] == [9999]  # inherited the upstream date ordinal, not 100


def test_process_once_session_derive_uses_configured_gap_minutes(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        session_gap_minutes=40,
    )
    _insert_audio_with_active_segments(
        config=config,
        segments=[
            ("seg_1", 0, 10_000),
            ("seg_2", 30 * 60 * 1000, 30 * 60 * 1000 + 10_000),
        ],
    )
    enqueue_task(config=config, task_type="session_derive", target_type="date_key", target_id="2087-05-10")

    result = process_once(
        config=config,
        run_id="run_session",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert result.task_type == "session_derive"
    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select segment_count from sessions")
    finally:
        conn.close()
    assert sessions == [{"segment_count": 2}]


def test_summarize_session_success_fans_in_to_daily_generate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    # auto-chain ON: this regression covers the full asr->...->daily_generate auto fan-in.
    config = AppConfig(
        data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", pipeline_auto_viewpoints=True
    )
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
            max_chunk_ms=1000,
        )

    summary_result = process_once(
        config=config,
        run_id="run_summary",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
        max_chunk_ms=1000,
    )

    assert summary_result.task_type == "summarize_session"
    assert summary_result.status == "succeeded"
    assert any(
        row["task_type"] == "daily_generate"
        and row["target_id"] == "2025-06-10"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )


def test_session_derive_does_not_enqueue_summarize_when_auto_off(tmp_path: Path) -> None:
    # With pipeline_auto_viewpoints=False (the default), the pipeline STOPS after
    # session_derive: a completed session_derive must NOT auto-enqueue summarize_session.
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="本地任务转写"),
            max_chunk_ms=1000,
        )

    # session_derive ran, but NO summarize_session was enqueued (manual now).
    assert any(row["task_type"] == "session_derive" and row["status"] == "succeeded" for row in process_status_rows(config=config))
    assert not any(row["task_type"] == "summarize_session" for row in process_status_rows(config=config))


def test_manual_summarize_does_not_enqueue_daily_when_auto_off(tmp_path: Path) -> None:
    # A MANUALLY enqueued summarize_session (slice 2's generate) must still run, but with
    # pipeline_auto_viewpoints=False it must NOT auto-enqueue daily_generate.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_with_active_segments(
        config=config,
        segments=[("seg_1", 0, 10_000)],
    )
    enqueue_task(config=config, task_type="session_derive", target_type="date_key", target_id="2087-05-10")
    process_once(
        config=config,
        run_id="run_session",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )
    # Manually enqueue summarize_session (as slice 2's generate route does).
    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select session_id from sessions")
    finally:
        conn.close()
    session_id = sessions[0]["session_id"]
    enqueue_task(config=config, task_type="summarize_session", target_type="session", target_id=session_id, priority=10)

    result = process_once(
        config=config,
        run_id="run_summary",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    # the manual summarize_session ran to success ...
    assert result.task_type == "summarize_session"
    assert result.status == "succeeded"
    # ... but it did NOT auto-enqueue daily_generate.
    assert not any(row["task_type"] == "daily_generate" for row in process_status_rows(config=config))


def test_process_once_session_summary_uses_injected_llm_adapter(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    # auto-chain ON so session_derive enqueues the summarize_session this test then runs.
    config = AppConfig(
        data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", pipeline_auto_viewpoints=True
    )
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
            max_chunk_ms=1000,
        )
    llm = RecordingSessionLLM()

    summary_result = process_once(
        config=config,
        run_id="run_summary_fake_llm",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
        llm=llm,
        max_chunk_ms=1000,
    )

    assert summary_result.task_type == "summarize_session"
    assert summary_result.status == "succeeded"
    assert llm.session_segments
    assert "wav" not in str(llm.session_segments).lower()
    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select content_json, prompt_version from summaries where summary_type = 'session'")
    finally:
        conn.close()
    assert "模拟 LLM session headline" in summaries[0]["content_json"]
    assert summaries[0]["prompt_version"] == "llm_port.session_summary.v1"


def _write_voice_wav(path: Path, seconds: float = 0.7, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(seconds * sample_rate)):
            sample = int(10_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))


def _insert_audio_with_active_segments(*, config: AppConfig, segments: list[tuple[str, int, int]]) -> None:
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
                "/source.wav",
                "/local.wav",
                "sha256:test",
                2_000_000,
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:00:00+08:00",
                "imported",
            ),
        )
        for segment_id, start_ms, end_ms in segments:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version, is_active
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_test",
                    f"chk_{segment_id}",
                    start_ms,
                    end_ms,
                    "测试片段",
                    "zh",
                    "self",
                    f"ev_{segment_id}",
                    0.99,
                    "MockASRAdapter",
                    "mock-asr",
                    "test",
                    1,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_cross_midnight_session_is_enqueued_for_summarize_and_daily(tmp_path: Path) -> None:
    # Regression: a session whose date_key is the day AFTER its file's recorded-day
    # (cross-midnight, §25.3 rule 2) must still be picked up by the session_derive ->
    # summarize_session fan-in (keyed by file-day) and routed to daily_generate for its
    # own date_key — otherwise it is silently orphaned (never summarized/published).
    from personal_context_node.process_runner import (
        _ready_daily_generate_dates_in_conn,
        _session_ids_for_day_in_conn,
    )
    from personal_context_node.sessions import derive_sessions_for_day

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status)
            values ('aud', 'DJI Mic 3', '/s.wav', '/l.wav', 'sha256:x', 5000000,
              '2087-05-10T23:00:00+08:00', '2087-05-11T10:00:00+08:00', 'imported')
            """
        )
        conn.execute(
            """
            insert into transcript_segments (segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version)
            values ('seg_late', 'aud', 'chk', 4200000, 4210000, 'cross midnight', 'zh', 'self', 'self',
              'ev', 0.9, 'mock', 'm', 'v')
            """
        )
        conn.commit()
    finally:
        conn.close()

    derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select session_id, date_key from sessions")
        # The session_derive ran for the file-day; its fan-in (keyed by file-day) must find it.
        summarize_targets = _session_ids_for_day_in_conn(conn, day="2087-05-10")
        daily_dates = _ready_daily_generate_dates_in_conn(conn, session_id=sessions[0]["session_id"])
    finally:
        conn.close()

    assert len(sessions) == 1
    assert sessions[0]["date_key"] == "2087-05-11"
    assert summarize_targets == [sessions[0]["session_id"]]
    assert daily_dates == ["2087-05-11"]


def test_retry_exhausted_summarize_session_does_not_block_daily_generate(tmp_path: Path) -> None:
    # Liveness: a summarize_session whose retries are exhausted (failed_retryable at
    # max_retries, e.g. repeated transient LLM failures) must not block the day's
    # daily_generate forever; the day proceeds with the sessions that succeeded.
    from personal_context_node.process_runner import _ready_daily_generate_dates_in_conn

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for sid in ("ses_ok", "ses_dead"):
            conn.execute(
                """
                insert into sessions (session_id, date_key, started_at, ended_at, source,
                  segment_count, active_speech_ms, first_segment_id, created_at, updated_at)
                values (?, '2087-05-10', '2087-05-10T08:00:00+08:00', '2087-05-10T08:10:00+08:00',
                  'derived_from_segments', 1, 1000, ?, 'now', 'now')
                """,
                (sid, "seg_" + sid),
            )
        conn.execute(
            "insert into tasks (task_id, task_type, target_type, target_id, status, retry_count, max_retries, available_at, created_at)"
            " values ('t_ok', 'summarize_session', 'session', 'ses_ok', 'succeeded', 0, 3, '', '')"
        )
        conn.execute(
            "insert into tasks (task_id, task_type, target_type, target_id, status, retry_count, max_retries, available_at, created_at)"
            " values ('t_dead', 'summarize_session', 'session', 'ses_dead', 'failed_retryable', 3, 3, '', '')"
        )
        conn.commit()
        ready = _ready_daily_generate_dates_in_conn(conn, session_id="ses_ok")
    finally:
        conn.close()
    assert ready == ["2087-05-10"]
