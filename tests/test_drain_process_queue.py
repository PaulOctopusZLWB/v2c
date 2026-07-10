from __future__ import annotations

from pathlib import Path

from personal_context_node import process_runner as _pr_module
from personal_context_node.adapters.asr.persistent_command import PersistentCommandASRAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import PipelineAdapters
from personal_context_node.process_runner import ProcessOnceResult, drain_process_queue
from personal_context_node.web.worker import PipelineWorker


def test_drain_empty_queue_reports_complete(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = drain_process_queue(config=config, vad=_Unused(), asr=_Unused(), llm=_Unused())
    assert result.status == "complete"
    assert result.process_steps == 0


def test_drain_stops_when_should_stop_true(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = drain_process_queue(config=config, vad=_Unused(), asr=_Unused(), llm=_Unused(), should_stop=lambda: True)
    assert result.status == "stopped"
    assert result.process_steps == 0


def test_web_worker_drains_more_than_default_max_steps(tmp_path: Path, monkeypatch) -> None:
    # A backlog larger than the old 200-step cap must drain in a single worker run.
    # We simulate 205 tasks by patching process_once to return "succeeded" 205 times,
    # then "no_task" — and assert the worker processes all 205 (not just 200).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    total_tasks = 205
    calls: dict[str, int] = {"n": 0}

    def fake_process_once(**kwargs) -> ProcessOnceResult:
        calls["n"] += 1
        if calls["n"] <= total_tasks:
            return ProcessOnceResult(task_id=f"t{calls['n']}", task_type="asr", status="succeeded")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    def fake_preview(**kwargs) -> ProcessOnceResult:
        # Return dry_run (work pending) until all tasks have been consumed.
        if calls["n"] < total_tasks:
            return ProcessOnceResult(task_id="peek", task_type="asr", status="dry_run")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    monkeypatch.setattr(_pr_module, "process_once", fake_process_once)
    monkeypatch.setattr(_pr_module, "preview_next_process_task", fake_preview)

    worker = PipelineWorker(config=config)
    result = worker.drain_now()

    assert result.status == "complete"
    assert result.tasks_succeeded == total_tasks


def test_web_worker_import_path_drains_more_than_default_max_steps(tmp_path: Path, monkeypatch) -> None:
    # The default non-blocking UI import path (start_import -> _import_then_drain) must ALSO
    # drain past the 200-step cap, not just the explicit /run path.
    import personal_context_node.web.worker as _worker_module

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    total_tasks = 205
    calls: dict[str, int] = {"n": 0}

    def fake_process_once(**kwargs) -> ProcessOnceResult:
        calls["n"] += 1
        if calls["n"] <= total_tasks:
            return ProcessOnceResult(task_id=f"t{calls['n']}", task_type="asr", status="succeeded")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    def fake_preview(**kwargs) -> ProcessOnceResult:
        if calls["n"] < total_tasks:
            return ProcessOnceResult(task_id="peek", task_type="asr", status="dry_run")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    monkeypatch.setattr(_pr_module, "process_once", fake_process_once)
    monkeypatch.setattr(_pr_module, "preview_next_process_task", fake_preview)
    monkeypatch.setattr(_worker_module, "import_audio_files", lambda **kwargs: None)

    worker = PipelineWorker(config=config)
    assert worker.start_import("ignored") is True
    worker._thread.join(timeout=30)

    assert worker._last_result is not None
    assert worker._last_result.tasks_succeeded == total_tasks


def test_web_worker_import_failure_still_drains_existing_queue(tmp_path: Path, monkeypatch) -> None:
    # Directory import can fail after some files were already registered by another ingest path.
    # The background UI worker must still drain whatever is already pending instead of leaving
    # the header stuck on pending tasks until someone manually presses Run.
    import personal_context_node.web.worker as _worker_module

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    calls: dict[str, int] = {"n": 0}

    def fake_process_once(**kwargs) -> ProcessOnceResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return ProcessOnceResult(task_id="t1", task_type="transcribe_diarize", status="succeeded")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    monkeypatch.setattr(_pr_module, "process_once", fake_process_once)
    monkeypatch.setattr(
        _worker_module,
        "import_audio_files",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("import failed after partial progress")),
    )

    worker = PipelineWorker(config=config)
    assert worker.start_import("ignored") is True
    worker._thread.join(timeout=30)

    assert worker._last_result is not None
    assert worker._last_result.status == "complete"
    assert worker._last_result.tasks_succeeded == 1


def test_drain_keeps_resident_asr_adapter_until_close_adapters(tmp_path: Path, monkeypatch) -> None:
    # A funasr_server drain owns a resident model subprocess. It intentionally SURVIVES the
    # drain (reloading the model per drain costs seconds-to-tens-of-seconds), and is reaped
    # by close_adapters() — which the web app shutdown hook calls — so nothing leaks past the
    # worker's lifetime. We prove both halves against the real subprocess.
    import personal_context_node.web.worker as _worker_module

    server = tmp_path / "resident.py"
    server.write_text("import sys\nfor _line in sys.stdin:\n    pass\n", encoding="utf-8")
    adapter = PersistentCommandASRAdapter(command=["python3", str(server)], timeout_seconds=10)
    proc = adapter._ensure()  # spawn the resident process up front
    assert proc.poll() is None  # alive before the drain

    build_calls = {"n": 0}

    def _build(**kwargs):
        build_calls["n"] += 1
        return PipelineAdapters(vad=_Unused(), asr=adapter, llm=_Unused())

    monkeypatch.setattr(_worker_module, "build_pipeline_adapters", _build)

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    worker = PipelineWorker(config=config)
    result = worker.drain_now()  # empty queue -> returns immediately, adapter stays resident

    assert result.status == "complete"
    assert adapter._proc is not None  # model subprocess survives the drain (resident cache)
    assert proc.poll() is None

    second = worker.drain_now()  # second drain reuses the cached adapters — no rebuild
    assert second.status == "complete"
    assert build_calls["n"] == 1

    worker.close_adapters()  # the app-shutdown path
    assert adapter._proc is None  # adapter forgot its process (close() ran)
    assert proc.poll() is not None  # the resident subprocess was actually terminated/reaped


def test_start_combined_extraction_runs_both_and_releases_both_adapters(tmp_path: Path, monkeypatch) -> None:
    # Combined extraction must call both embed_fn and classify_fn per pending segment, write both
    # artifacts, and release BOTH resident adapters when done (two models resident at once here).
    from personal_context_node import transcription as _transcription
    from personal_context_node.speaker_embeddings import get_embeddings, put_embeddings_bulk
    from personal_context_node.segment_emotions import get_emotions

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2"])
    monkeypatch.setattr(
        _transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )

    class FakeEmbedAdapter:
        def __init__(self):
            self.closed = False

        def embed(self, path: str) -> list[float]:
            return [0.1, 0.2, 0.3]

        def close(self) -> None:
            self.closed = True

    class FakeEmotionAdapter:
        def __init__(self):
            self.closed = False

        def classify(self, path: str) -> dict:
            return {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

        def close(self) -> None:
            self.closed = True

    embed_adapter = FakeEmbedAdapter()
    emotion_adapter = FakeEmotionAdapter()

    worker = PipelineWorker(config=config)
    started = worker.start_combined_extraction(
        embed_factory=lambda: embed_adapter, classify_factory=lambda: emotion_adapter,
    )
    assert started is True
    worker._thread.join(timeout=30)

    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2"])) == {"seg_1", "seg_2"}
    assert set(get_emotions(config=config, segment_ids=["seg_1", "seg_2"])) == {"seg_1", "seg_2"}
    assert embed_adapter.closed is True
    assert emotion_adapter.closed is True
    assert worker.embedding_state()["active"] is False
    assert worker.emotion_state()["active"] is False


def test_start_combined_extraction_guards_against_double_spawn(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    worker = PipelineWorker(config=config)

    release = __import__("threading").Event()

    class BlockingEmbedAdapter:
        def embed(self, path: str) -> list[float]:
            release.wait(timeout=5)
            return [0.1, 0.2, 0.3]

        def close(self) -> None:
            pass

    class NeverCalledEmotionAdapter:
        def classify(self, path: str) -> dict:
            raise AssertionError("no pending segments -> classify must not be called")

        def close(self) -> None:
            pass

    started_first = worker.start_combined_extraction(
        embed_factory=lambda: BlockingEmbedAdapter(), classify_factory=lambda: NeverCalledEmotionAdapter(),
    )
    assert started_first is True
    # A second start while the first thread is (nominally) still the "running" guard must refuse.
    started_second = worker.start_combined_extraction(
        embed_factory=lambda: BlockingEmbedAdapter(), classify_factory=lambda: NeverCalledEmotionAdapter(),
    )
    assert started_second is False

    release.set()
    worker._thread.join(timeout=30)


def test_start_combined_extraction_releases_adapters_on_failure(tmp_path: Path, monkeypatch) -> None:
    # A raising embed_fn must still release BOTH adapters (finally-block guarantee), and must not
    # leave embedding_state()/emotion_state() stuck with active=True.
    from personal_context_node import transcription as _transcription

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])
    monkeypatch.setattr(
        _transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )

    class BoomEmbedAdapter:
        def __init__(self):
            self.closed = False

        def embed(self, path: str) -> list[float]:
            raise RuntimeError("boom")

        def close(self) -> None:
            self.closed = True

    class FakeEmotionAdapter:
        def __init__(self):
            self.closed = False

        def classify(self, path: str) -> dict:
            return {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

        def close(self) -> None:
            self.closed = True

    embed_adapter = BoomEmbedAdapter()
    emotion_adapter = FakeEmotionAdapter()

    worker = PipelineWorker(config=config)
    assert worker.start_combined_extraction(
        embed_factory=lambda: embed_adapter, classify_factory=lambda: emotion_adapter,
    ) is True
    worker._thread.join(timeout=30)

    # embed_fn raising is caught per-segment by extract_pending_embeddings_and_emotions (failed,
    # not a crash), so the run still completes normally and both adapters are released.
    assert embed_adapter.closed is True
    assert emotion_adapter.closed is True
    assert worker.embedding_state()["active"] is False
    assert worker.emotion_state()["active"] is False


def _insert_session_with_segments(
    database_path: Path,
    segment_ids: list[str],
    *,
    session_id: str = "ses_test",
    audio_file_id: str = "aud_test",
    date_key: str = "2087-05-10",
) -> None:
    from personal_context_node.storage.sqlite import connect, initialize

    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (audio_file_id, "DJI Mic 3", f"/source/{audio_file_id}.wav", 1, 1, f"/raw/{audio_file_id}.wav", f"sha256:{audio_file_id}", 2000, f"{date_key}T08:00:00+08:00", f"{date_key}T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, date_key, f"{date_key}T08:00:00+08:00", f"{date_key}T08:00:02+08:00", "derived_from_segments", len(segment_ids), 2000, segment_ids[0], f"{date_key}T08:00:03+08:00", f"{date_key}T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(segment_ids):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, audio_file_id, f"chk_{segment_id}", session_id, index * 1000, (index + 1) * 1000, f"{date_key}T08:00:0{index}.000000+08:00", f"{date_key}T08:00:0{index + 1}.000000+08:00", f"text {index + 1}", "zh", "self", "self", f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, f"{date_key}T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


class _Unused:
    pass
