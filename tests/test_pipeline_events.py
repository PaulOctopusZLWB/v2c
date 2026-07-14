from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.pipeline_events import (
    EventCursor,
    active_feature_progress,
    derive_tick_events,
    estimate_pipeline_eta,
    fetch_new_segments,
    max_segment_rowid,
)
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


def test_active_feature_progress_reports_artifact_coverage_and_file_name(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('a','d','/inbox/current.wav',1,1,'/raw/current.wav','sha256:x',1,'x','x','imported')"
        )
        for index in range(3):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"s{index}", "a", f"c{index}", None, index * 1000, (index + 1) * 1000, "x", "zh", "spk_1", "spk_1", f"e{index}", 0.9, "m", "m", "v", 1, "x"),
            )
        conn.execute(
            "insert into segment_embeddings (segment_id, model, dim, vector, created_at) values ('s0','cam++',1,x'00','x'), ('s1','cam++',1,x'00','x')"
        )
        conn.execute(
            "insert into segment_emotions (segment_id, model, label, scores_json, created_at) values ('s0','emotion2vec','neutral','{}','x')"
        )
        conn.execute(
            "insert into tasks (task_id, task_type, target_type, target_id, status, started_at, created_at) values ('t','extract_features','audio_file','a','running',datetime('now','-65 seconds'),'x')"
        )
        conn.commit()
    finally:
        conn.close()

    progress = active_feature_progress(config=config)

    assert progress is not None
    assert progress["current"] == "current.wav"
    assert progress["total_segments"] == 3
    assert progress["embedded"] == 2
    assert progress["emoted"] == 1
    assert progress["done"] == 3
    assert progress["total"] == 6
    assert int(progress["elapsed_seconds"]) >= 65


def test_pipeline_eta_uses_live_feature_ratio_and_serial_gpu_lane() -> None:
    rows: list[dict[str, object]] = [
        {"task_type": "extract_features", "status": "succeeded", "duration_ms": 600_000},
        {"task_type": "extract_features", "status": "succeeded", "duration_ms": 660_000},
        {"task_type": "extract_features", "status": "succeeded", "duration_ms": 720_000},
        {"task_type": "extract_features", "status": "running", "duration_ms": None},
        {"task_type": "extract_features", "status": "pending", "duration_ms": None},
        {"task_type": "identify_speakers", "status": "succeeded", "duration_ms": 2_000},
        {"task_type": "identify_speakers", "status": "pending", "duration_ms": None},
    ]
    feature = {"done": 50, "total": 100, "elapsed_seconds": 300}

    eta_seconds, confidence = estimate_pipeline_eta(rows=rows, feature_progress=feature)

    # Active feature: max(live 300s, historical remaining 330s); pending feature: 660s.
    # GPU work is serial, so the lane ETA is 990s. The 2s CPU lane runs alongside it.
    assert eta_seconds == 990
    assert confidence == "live"
