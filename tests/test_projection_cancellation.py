from __future__ import annotations

import multiprocessing
import threading
import time
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import put_embeddings_bulk
from personal_context_node.web import projection_cancellation as jobs
from personal_context_node.web.app import create_app


def _slow_fit_worker(pipe, warm) -> None:
    time.sleep(30)


@pytest.fixture(autouse=True)
def clean_worker():
    jobs.stop_projection_worker()
    yield
    jobs.stop_projection_worker()


def test_cancel_before_projection_is_registered() -> None:
    job_id = uuid4().hex
    jobs.cancel(job_id)
    with pytest.raises(ValueError, match="取消"):
        jobs.register(job_id)


def test_cancel_stops_only_the_projection_worker() -> None:
    job_id = uuid4().hex
    job = jobs.register(job_id)
    process = multiprocessing.get_context("spawn").Process(target=time.sleep, args=(30,))
    process.start()
    job.process = process
    try:
        jobs.cancel(job_id)
        process.join(timeout=5)
        assert job.cancelled.is_set()
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        jobs.finish(job_id, job)


def test_projection_worker_returns_coordinates_without_database_access() -> None:
    job_id = uuid4().hex
    job = jobs.register(job_id)
    try:
        matrix = np.array([[1., 0.], [0., 1.], [0.8, 0.2]], dtype=np.float32)
        coords, method = jobs.fit(job, matrix, db_path="unused", ids=["a", "b", "c"], method="pca")
        assert coords.shape == (3, 2)
        assert method == "pca"
    finally:
        jobs.finish(job_id, job)


def test_worker_is_reused_and_cached_coordinates_do_not_restart_it() -> None:
    first_id, second_id = uuid4().hex, uuid4().hex
    first, second = jobs.register(first_id), jobs.register(second_id)
    try:
        matrix = np.array([[1., 0.], [0., 1.], [0.8, 0.2]], dtype=np.float32)
        kwargs = dict(db_path="worker-test", ids=["a", "b", "c"], method="pca")
        jobs.fit(first, matrix, **kwargs)
        process = jobs._WORKER
        assert process is not None and process.is_alive()
        jobs.fit(second, matrix, **kwargs)
        assert jobs._WORKER is process and process.is_alive()
        jobs.fit(second, matrix, **{**kwargs, "db_path": "another-scope"})
        assert jobs._WORKER is process and process.is_alive()
    finally:
        jobs.finish(first_id, first)
        jobs.finish(second_id, second)


def test_cancelling_one_fit_does_not_cancel_a_waiting_fit(monkeypatch) -> None:
    first_id, second_id = uuid4().hex, uuid4().hex
    first, second = jobs.register(first_id), jobs.register(second_id)
    original_worker = jobs._fit_worker
    monkeypatch.setattr(jobs, "_fit_worker", _slow_fit_worker)
    matrix = np.array([[1., 0.], [0., 1.], [0.8, 0.2]], dtype=np.float32)
    results = {}
    def run(name, job):
        try:
            results[name] = jobs.fit(job, matrix, db_path=name, ids=["a", "b", "c"], method="pca")[1]
        except ValueError as exc:
            results[name] = str(exc)
    one = threading.Thread(target=run, args=("first", first), daemon=True)
    two = threading.Thread(target=run, args=("second", second), daemon=True)
    try:
        one.start()
        deadline = time.monotonic() + 5
        while first.process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first.process is not None
        monkeypatch.setattr(jobs, "_fit_worker", original_worker)
        two.start()
        jobs.cancel(first_id)
        one.join(timeout=8)
        two.join(timeout=8)
        assert not one.is_alive() and not two.is_alive()
        assert "取消" in results["first"] and results["second"] == "pca"
    finally:
        jobs.finish(first_id, first)
        jobs.finish(second_id, second)


def test_cancel_api_terminates_inflight_fit_without_writing_database(tmp_path, monkeypatch) -> None:
    from test_speaker_embeddings import _insert_session_with_segments
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    ids = [f"segment_{index}" for index in range(5)]
    _insert_session_with_segments(config.database_path, ids)
    put_embeddings_bulk(config=config, items=[(segment, [1.0, 0.1]) for segment in ids])
    client = TestClient(create_app(config=config))
    monkeypatch.setattr(jobs, "_fit_worker", _slow_fit_worker)
    job_id = uuid4().hex
    result = {}
    def run():
        result["response"] = client.post("/api/speakers/projection", json={"session_ids": ["ses_test"], "projection_job_id": job_id})
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    process = None
    while time.monotonic() < deadline:
        job = jobs._JOBS.get(job_id)
        if job and job.process is not None and job.process.is_alive():
            process = job.process
            break
        time.sleep(0.02)
    assert process is not None, "projection worker never started"
    assert client.post(f"/api/speakers/projection-cancel/{job_id}").json() == {"cancelled": True}
    thread.join(timeout=8)
    assert not thread.is_alive()
    assert not process.is_alive()
    assert result["response"].status_code == 400
    assert "取消" in result["response"].json()["detail"]
    counts = client.post("/api/speakers/projection-scope", json={"session_ids": ["ses_test"]}).json()
    assert counts["total"] == counts["embedded"] == 5
