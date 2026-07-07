from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.pipeline_events import EventCursor, derive_tick_events, fetch_new_segments, max_segment_rowid
from personal_context_node.storage.sqlite import connect, initialize


def _is_failed(row: dict[str, object]) -> bool:
    return str(row["status"]).startswith("failed")


def _summary(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "active_stage": None,
        "current_target": None,
        "worker_running": False,
        "done_total": 0,
        "total": 0,
        "failed_total": 0,
        "eta_seconds": None,
        "import_progress": None,
    }
    base.update(over)
    return base


def _tick(cursor: EventCursor, *, rows: list[dict[str, object]] | None = None, segments=None, changed=True, **summary_over):
    return derive_tick_events(
        cursor=cursor,
        rows=rows or [],
        summary=_summary(**summary_over),
        summary_changed=changed,
        is_failed=_is_failed,
        new_segments=segments or [],
    )


def test_stage_changed_and_progress_sequence() -> None:
    cursor = EventCursor()
    running = [{"task_id": "t1", "task_type": "asr", "target_id": "a1", "status": "running"}]

    first = _tick(cursor, rows=running, active_stage="asr", current_target="a1", worker_running=True, done_total=1, total=4)
    names = [n for n, _ in first]
    assert names == ["stage.changed", "task.progress"]
    assert first[0][1] == {"stage": "asr", "previous": None, "target": "a1"}

    # Same stage again, summary unchanged -> no events.
    assert _tick(cursor, rows=running, active_stage="asr", worker_running=True, changed=False) == []

    # Stage advances -> stage.changed (previous carried).
    nxt = _tick(cursor, rows=running, active_stage="session_derive", worker_running=True)
    assert nxt[0] == ("stage.changed", {"stage": "session_derive", "previous": "asr", "target": None})


def test_task_failed_only_for_new_failures() -> None:
    cursor = EventCursor()
    ok = {"task_id": "t1", "task_type": "asr", "target_id": "a1", "status": "succeeded", "last_error": None}
    bad = {"task_id": "t2", "task_type": "asr", "target_id": "a2", "status": "failed_terminal", "last_error": "timed out"}

    # First tick establishes the baseline: pre-existing failures do NOT re-fire.
    assert _tick(cursor, rows=[ok, bad]) == []
    # No change -> still nothing.
    assert _tick(cursor, rows=[ok, bad], changed=False) == []
    # A NEW failure fires exactly once, with the error attached.
    bad2 = {"task_id": "t3", "task_type": "vad", "target_id": "a3", "status": "failed_terminal", "last_error": "boom"}
    events = _tick(cursor, rows=[ok, bad, bad2], changed=False)
    assert events == [("task.failed", {"task_id": "t3", "task_type": "vad", "target_id": "a3", "error": "boom"})]
    assert _tick(cursor, rows=[ok, bad, bad2], changed=False) == []


def test_run_completed_fires_once_after_activity() -> None:
    cursor = EventCursor()
    running = [{"task_id": "t1", "task_type": "asr", "target_id": "a1", "status": "running"}]
    done = [{"task_id": "t1", "task_type": "asr", "target_id": "a1", "status": "succeeded"}]

    # Idle stream from the start: never completes.
    assert _tick(cursor, rows=[], changed=False) == []
    # Activity...
    _tick(cursor, rows=running, active_stage="asr", worker_running=True)
    # ...then everything settled + worker idle -> run.completed once.
    events = _tick(cursor, rows=done, done_total=1, total=1, changed=True)
    assert ("run.completed", {"total": 1, "done_total": 1, "failed_total": 0}) in events
    assert _tick(cursor, rows=done, changed=False) == []


def test_segment_cursor_streams_only_new_rows(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('a','d','/s',1,1,'/r','sha256:x',1,'x','x','imported')"
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values ('old','a','c1',null,0,1000,'旧段','zh','spk_1','spk_1','e_old',0.9,'m','m','v',1,'x')"
        )
        conn.commit()
    finally:
        conn.close()

    # 连接时游标 = 当前 max rowid:不回放历史段。
    start = max_segment_rowid(config=config)
    events, cursor = fetch_new_segments(config=config, after_rowid=start)
    assert events == [] and cursor == start

    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values ('new1','a','c2',null,1000,2000,'新段一','zh','spk_1','spk_1','e_n1',0.9,'m','m','v',1,'x')"
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values ('gone','a','c3',null,2000,3000,'已停用','zh','spk_1','spk_1','e_g',0.9,'m','m','v',0,'x')"
        )
        conn.commit()
    finally:
        conn.close()

    events, cursor2 = fetch_new_segments(config=config, after_rowid=cursor)
    assert [e["segment_id"] for e in events] == ["new1"]  # is_active=0 的不下发
    assert events[0]["text"] == "新段一"
    assert cursor2 > cursor
    # 游标推进后不重复。
    again, _ = fetch_new_segments(config=config, after_rowid=cursor2)
    assert again == []
