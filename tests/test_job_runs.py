from __future__ import annotations

from personal_context_node.config import AppConfig
from personal_context_node.jobs import job_status_rows, record_job_run


def test_record_job_run_tracks_success_and_failure(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    success = record_job_run(config=config, job_name="health", operation=lambda: "ok")

    assert success.result == "ok"
    assert success.status == "succeeded"

    try:
        record_job_run(config=config, job_name="broken", operation=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    except RuntimeError:
        pass
    else:
        raise AssertionError("record_job_run swallowed the original error")

    rows = job_status_rows(config=config, limit=10)
    by_name = {row["job_name"]: row for row in rows}
    assert by_name["health"]["status"] == "succeeded"
    assert by_name["broken"]["status"] == "failed"
    assert "boom" in by_name["broken"]["error"]
    assert by_name["health"]["run_id"].startswith("run_")
