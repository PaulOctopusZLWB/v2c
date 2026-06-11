from __future__ import annotations

from datetime import datetime, timedelta, timezone

from personal_context_node.config import AppConfig
from personal_context_node.tasks import (
    claim_next_task,
    enqueue_task,
    fail_task,
    process_status_rows,
    reclaim_expired_tasks,
    rerun_task,
    retry_task,
    start_task,
    succeed_task,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_task_lifecycle_deduplicates_and_tracks_claims(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    first = enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_1")
    second = enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_1")

    assert first.task_id == second.task_id
    assert first.created is True
    assert second.created is False

    claimed = claim_next_task(config=config, task_type="vad", run_id="run_1")

    assert claimed is not None
    assert claimed.task_id == first.task_id
    assert claimed.status == "claimed"
    assert claimed.claimed_by_run_id == "run_1"

    start_task(config=config, task_id=claimed.task_id)
    succeed_task(config=config, task_id=claimed.task_id)

    rows = process_status_rows(config=config)
    assert rows == [
        {
            "task_id": first.task_id,
            "task_type": "vad",
            "target_type": "audio_file",
            "target_id": "aud_1",
            "status": "succeeded",
            "attempt_count": 1,
            "last_error": None,
            "duration_ms": rows[0]["duration_ms"],
            "model_name": None,
            "model_version": None,
        }
    ]
    assert isinstance(rows[0]["duration_ms"], int)


def test_process_status_rows_include_task_duration_ms(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into tasks (
              task_id, task_type, target_type, target_id, status, attempt_count,
              started_at, finished_at, available_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_duration",
                "asr",
                "audio_chunk",
                "chk_1",
                "succeeded",
                1,
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:02.500000+00:00",
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:02.500000+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    rows = process_status_rows(config=config)

    assert rows[0]["duration_ms"] == 2500


def test_process_status_rows_include_asr_model_version(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into tasks (
              task_id, task_type, target_type, target_id, status,
              available_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_asr_model",
                "asr",
                "audio_chunk",
                "chk_1",
                "succeeded",
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_1",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:00+00:00",
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
                "seg_1",
                "aud_1",
                "chk_1",
                0,
                1000,
                "本地转写。",
                "zh",
                "self",
                "ev_1",
                0.99,
                "CommandASRAdapter",
                "sensevoice",
                "local-2026-06",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    rows = process_status_rows(config=config)

    assert rows[0]["model_name"] == "sensevoice"
    assert rows[0]["model_version"] == "local-2026-06"


def test_enqueue_task_rejects_unknown_task_type(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    try:
        enqueue_task(config=config, task_type="unknown", target_type="audio_file", target_id="aud_1")
    except ValueError as exc:
        assert "unknown task_type" in str(exc)
    else:
        raise AssertionError("enqueue_task accepted an unknown task_type")


def test_enqueue_task_accepts_declared_archive_task_type(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    result = enqueue_task(config=config, task_type="archive", target_type="audio_file", target_id="aud_1")

    assert result.created is True


def test_claim_next_task_uses_available_at_and_priority_with_lease(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for task_id, target_id, priority, available_at in [
            ("task_low", "chk_low", 100, now),
            ("task_high", "chk_high", 10, now),
            ("task_future", "chk_future", 1, future),
        ]:
            conn.execute(
                """
                insert into tasks (
                  task_id, task_type, target_type, target_id, status, priority,
                  available_at, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, "asr", "audio_chunk", target_id, "pending", priority, available_at, now, now),
            )
        conn.commit()
    finally:
        conn.close()

    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1", lease_seconds=60)

    assert claimed is not None
    assert claimed.task_id == "task_high"
    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select task_id, retry_count, attempt_count, claimed_by_run_id, lease_expires_at
            from tasks
            where task_id = ?
            """,
            ("task_high",),
        )
    finally:
        conn.close()
    assert rows[0]["retry_count"] == 1
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["claimed_by_run_id"] == "run_1"
    assert rows[0]["lease_expires_at"]


def test_failed_retryable_and_lease_reclaim(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None

    fail_task(config=config, task_id=task.task_id, error="model unavailable", terminal=False)
    retry = claim_next_task(config=config, task_type="asr", run_id="run_2", lease_seconds=60)

    assert retry is not None
    assert retry.status == "claimed"
    assert retry.attempt_count == 2

    expired = datetime.now(timezone.utc) - timedelta(hours=2)
    reclaimed = reclaim_expired_tasks(config=config, lease_seconds=60, now=expired + timedelta(hours=2, minutes=1))

    assert reclaimed == 1
    rows = process_status_rows(config=config)
    assert rows[0]["status"] == "pending"


def test_claim_next_task_skips_retryable_tasks_at_max_retries(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", task_max_retries=1)
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    fail_task(config=config, task_id=task.task_id, error="model unavailable", terminal=False)

    retry = claim_next_task(config=config, task_type="asr", run_id="run_2")

    assert retry is None
    rows = process_status_rows(config=config)
    assert rows[0]["status"] == "failed_retryable"
    conn = connect(config.database_path)
    try:
        stored = fetch_all(conn, "select retry_count, max_retries from tasks where task_id = ?", (task.task_id,))
    finally:
        conn.close()
    assert stored == [{"retry_count": 1, "max_retries": 1}]


def test_retry_task_resets_failed_task_to_pending(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    fail_task(config=config, task_id=task.task_id, error="model unavailable", terminal=True)

    result = retry_task(config=config, task_id=task.task_id)

    assert result.task_id == task.task_id
    assert result.status == "pending"
    rows = process_status_rows(config=config)
    assert rows[0]["status"] == "pending"
    assert rows[0]["last_error"] is None


def test_rerun_task_reopens_existing_target_task(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    succeed_task(config=config, task_id=task.task_id)

    result = rerun_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")

    assert result.task_id == task.task_id
    assert result.created is False
    rows = process_status_rows(config=config)
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempt_count"] == 0
