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
        }
    ]


def test_failed_retryable_and_lease_reclaim(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None

    fail_task(config=config, task_id=task.task_id, error="model unavailable", terminal=False)
    retry = claim_next_task(config=config, task_type="asr", run_id="run_2")

    assert retry is not None
    assert retry.status == "claimed"
    assert retry.attempt_count == 2

    expired = datetime.now(timezone.utc) - timedelta(hours=2)
    reclaimed = reclaim_expired_tasks(config=config, lease_seconds=60, now=expired + timedelta(hours=2, minutes=1))

    assert reclaimed == 1
    rows = process_status_rows(config=config)
    assert rows[0]["status"] == "pending"


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
