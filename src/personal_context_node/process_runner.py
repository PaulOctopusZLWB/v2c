from __future__ import annotations

from dataclasses import dataclass

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRPort
from personal_context_node.core.ports.vad import VADPort
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.obsidian_publish import publish_obsidian_day
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.storage.sqlite import connect, fetch_all
from personal_context_node.tasks import claim_next_task, enqueue_task, fail_task, start_task, succeed_task
from personal_context_node.transcription import transcribe_pending_chunks


@dataclass(frozen=True)
class ProcessOnceResult:
    task_id: str | None
    task_type: str | None
    status: str


def process_once(
    *,
    config: AppConfig,
    run_id: str,
    vad: VADPort,
    asr: ASRPort,
    max_chunk_ms: int = 30_000,
) -> ProcessOnceResult:
    task = claim_next_task(config=config, task_type="vad", run_id=run_id)
    if task is None:
        task = claim_next_task(config=config, task_type="asr", run_id=run_id)
    if task is None:
        task = claim_next_task(config=config, task_type="session_derive", run_id=run_id)
    if task is None:
        task = claim_next_task(config=config, task_type="daily_generate", run_id=run_id)
    if task is None:
        task = claim_next_task(config=config, task_type="obsidian_publish", run_id=run_id)
    if task is None:
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    try:
        start_task(config=config, task_id=task.task_id)
        if task.task_type == "vad":
            preprocess_imported_audio(config=config, vad=vad, max_chunk_ms=max_chunk_ms, audio_file_id=task.target_id)
            for chunk_id in _chunk_ids_for_audio_file(config=config, audio_file_id=task.target_id):
                enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id=chunk_id)
        elif task.task_type == "asr":
            transcribe_pending_chunks(config=config, asr=asr, chunk_id=task.target_id)
            for date_key in _ready_session_derive_dates(config=config, chunk_id=task.target_id):
                enqueue_task(config=config, task_type="session_derive", target_type="date_key", target_id=date_key)
        elif task.task_type == "session_derive":
            derive_sessions_for_day(config=config, day=task.target_id)
            enqueue_task(config=config, task_type="daily_generate", target_type="date_key", target_id=task.target_id)
        elif task.task_type == "daily_generate":
            generate_daily_context(config=config, day=task.target_id, llm=RuleBasedLLMAdapter())
            enqueue_task(config=config, task_type="obsidian_publish", target_type="date_key", target_id=task.target_id)
        elif task.task_type == "obsidian_publish":
            publish_obsidian_day(config=config, day=task.target_id)
        else:
            raise ValueError(f"unsupported task type: {task.task_type}")
        succeed_task(config=config, task_id=task.task_id)
        return ProcessOnceResult(task_id=task.task_id, task_type=task.task_type, status="succeeded")
    except Exception as exc:
        fail_task(config=config, task_id=task.task_id, error=str(exc), terminal=False)
        raise


def _chunk_ids_for_audio_file(*, config: AppConfig, audio_file_id: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            "select chunk_id from audio_chunks where audio_file_id = ? order by source_start_ms",
            (audio_file_id,),
        )
        return [str(row["chunk_id"]) for row in rows]
    finally:
        conn.close()


def _ready_session_derive_dates(*, config: AppConfig, chunk_id: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select ac.audio_file_id, substr(af.recorded_at, 1, 10) as date_key
            from audio_chunks ac
            join audio_files af on af.audio_file_id = ac.audio_file_id
            where ac.chunk_id = ?
            """,
            (chunk_id,),
        )
        if not rows:
            return []
        audio_file_id = str(rows[0]["audio_file_id"])
        pending = fetch_all(
            conn,
            "select chunk_id from audio_chunks where audio_file_id = ? and status != 'transcribed'",
            (audio_file_id,),
        )
        if pending:
            return []
        return sorted({str(row["date_key"]) for row in rows})
    finally:
        conn.close()
