from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.segment_emotions import put_emotions_bulk
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


class _StubEmotionAdapter:
    """Stand-in for PersistentCommandEmotionAdapter: returns a fixed emotion, records close()."""

    def __init__(self, emotion: dict) -> None:
        self._emotion = emotion
        self.closed = False

    def classify(self, audio_path: str) -> dict:
        return dict(self._emotion)

    def close(self) -> None:
        self.closed = True


def _wait_for(predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def test_emotion_status_counts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    # Classify exactly one of the three active segments.
    put_emotions_bulk(
        config=config, items=[("seg_a", {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}})]
    )
    client = TestClient(create_app(config=config))

    response = client.get("/api/emotions/status")
    assert response.status_code == 200
    assert response.json() == {"total": 3, "emoted": 1, "pending": 2}

    # Scoping by session_id keeps the same three-segment scope (all share ses_lab).
    scoped = client.get("/api/emotions/status", params={"session_id": "ses_lab"})
    assert scoped.status_code == 200
    assert scoped.json() == {"total": 3, "emoted": 1, "pending": 2}

    # A session with no segments yields all zeros.
    empty = client.get("/api/emotions/status", params={"session_id": "ses_missing"})
    assert empty.status_code == 200
    assert empty.json() == {"total": 0, "emoted": 0, "pending": 0}


def test_extract_emotions_starts(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    app = create_app(config=config)
    client = TestClient(app)

    # DI seam: inject a stub emotion adapter + a stub segment audio path so NO real model and NO
    # real audio slice is needed. segment_audio_path is imported lazily inside
    # extract_pending_emotions, so patch it at its definition module.
    stub = _StubEmotionAdapter({"label": "中立/neutral", "scores": {"中立/neutral": 1.0}})
    app.state.worker._emotion_factory = lambda: stub
    monkeypatch.setattr(
        "personal_context_node.transcription.segment_audio_path",
        lambda *, config, segment_id: tmp_path / f"{segment_id}.wav",
    )

    before = client.get("/api/emotions/status").json()
    assert before["pending"] == 3

    started = client.post("/api/emotions/extract", json={})
    assert started.status_code == 200
    assert started.json() == {"started": True}

    # Background thread runs to completion: pending drops to 0 and the adapter was closed.
    _wait_for(lambda: not app.state.worker.is_running())
    after = client.get("/api/emotions/status").json()
    assert after["pending"] == 0
    assert after["emoted"] == 3
    assert stub.closed is True


def test_extract_emotions_returns_false_when_running(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    app = create_app(config=config)
    worker = app.state.worker

    # A factory whose adapter blocks until released, so the first run is still "running".
    import threading

    release = threading.Event()

    class _BlockingAdapter:
        def __init__(self) -> None:
            self.closed = False

        def classify(self, audio_path: str) -> dict:
            release.wait(5.0)
            return {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

        def close(self) -> None:
            self.closed = True

    blocking = _BlockingAdapter()
    worker._emotion_factory = lambda: blocking
    monkeypatch.setattr(
        "personal_context_node.transcription.segment_audio_path",
        lambda *, config, segment_id: tmp_path / f"{segment_id}.wav",
    )

    assert worker.start_emotion_extraction() is True
    _wait_for(lambda: worker.emotion_state() is not None and worker.emotion_state().get("active"))
    # Second call while the first is still running is rejected.
    assert worker.start_emotion_extraction() is False
    release.set()
    _wait_for(lambda: not worker.is_running())
    assert blocking.closed is True


def _insert_labeling_day(database_path: Path) -> None:
    """Seed one session with three active segments (spk_a x2, spk_b x1) plus an inactive one."""
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_lab", "DJI Mic 3", "/source/lab.wav", 1, 1, "/raw/lab.wav", "sha256:lab", 5000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_lab", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:05+08:00", "derived_from_segments", 3, 1500, "seg_a", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        segments = [
            ("seg_a", "spk_a", 0, 500, "2087-05-10T08:00:00+08:00", "alice one", 1),
            ("seg_b", "spk_a", 500, 1000, "2087-05-10T08:00:01+08:00", "alice two", 1),
            ("seg_c", "spk_b", 1000, 1500, "2087-05-10T08:00:02+08:00", "bob one", 1),
            ("seg_inactive", "spk_c", 1500, 2000, "2087-05-10T08:00:03+08:00", "stale", 0),
        ]
        for segment_id, speaker, start_ms, end_ms, abs_start, text, is_active in segments:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_lab", "chk_lab", "ses_lab", start_ms, end_ms, abs_start, text, "zh", speaker, speaker, f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", is_active, "2087-05-10T08:00:02+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
