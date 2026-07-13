from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRResult, ASRSegment
from personal_context_node.process_runner import (
    CPU_TASK_TYPES,
    GPU_TASK_TYPES,
    PIPELINE,
    PROCESS_TASK_ORDER,
    process_once,
)
from personal_context_node.speaker_embeddings import get_embeddings
from personal_context_node.segment_emotions import get_emotions
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task, process_status_rows


class _FakeDiarizedASR:
    model_name = "fake-diarized-asr"
    model_version = "test"

    def transcribe(self, audio_path: Path) -> ASRResult:
        return ASRResult(
            segments=[
                ASRSegment(text="你好", start_ms=0, end_ms=900, language="zh", speaker="self"),
                ASRSegment(text="世界", start_ms=1000, end_ms=1900, language="zh", speaker="spk_01"),
            ],
            backend="fake",
            model_name=self.model_name,
            model_version=self.model_version,
            language="zh",
        )


class _FakeEmbedAdapter:
    def __init__(self):
        self.batch_calls: list[list[tuple[str, str]]] = []

    def embed(self, path: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, items: list[tuple[str, str]]) -> list[dict]:
        self.batch_calls.append(list(items))
        return [{"segment_id": sid, "embedding": [0.1, 0.2, 0.3]} for sid, _ in items]

    def close(self) -> None:
        pass


class _FakeEmotionAdapter:
    def classify(self, path: str) -> dict:
        return {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    def classify_batch(self, items: list[tuple[str, str]]) -> list[dict]:
        return [{"segment_id": sid, "label": "中立/neutral", "scores": {"中立/neutral": 1.0}} for sid, _ in items]

    def close(self) -> None:
        pass


def _write_pcm_wav(path: Path, *, ms: int = 3000, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * (rate * ms // 1000))


def _seed_audio_file(config: AppConfig, *, audio_file_id: str, raw_path: Path) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (audio_file_id, "DJI Mic 3", f"/src/{audio_file_id}.wav", str(raw_path), f"sha:{audio_file_id}", 3000, "2026-06-14T09:00:00+08:00", "2026-06-14T09:30:00+08:00", "imported"),
        )
        conn.commit()
    finally:
        conn.close()


def _tasks_by_type(config: AppConfig, task_type: str) -> list[dict]:
    return [r for r in process_status_rows(config=config) if r["task_type"] == task_type]


def test_extract_features_is_a_pure_leaf_in_the_gpu_lane() -> None:
    # No edge may have extract_features as UPSTREAM (a leaf gates nothing), and no session_derive
    # readiness predicate references it (its upstreams still fan into session_derive directly).
    assert not any(edge.upstream_task_type == "extract_features" for edge in PIPELINE)
    downstreams = {edge.downstream_task_type for edge in PIPELINE if edge.upstream_task_type in ("transcribe_diarize", "asr")}
    assert {"session_derive", "extract_features"} <= downstreams
    # Resident MPS subprocess pair -> pinned to the GPU lane, last in claim order (never starves ASR).
    assert "extract_features" in GPU_TASK_TYPES
    assert "extract_features" not in CPU_TASK_TYPES
    assert PROCESS_TASK_ORDER[-1] == "extract_features"


def test_transcribe_diarize_fans_out_extract_features(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize")
    raw = tmp_path / "raw" / "a.wav"
    _write_pcm_wav(raw)
    _seed_audio_file(config, audio_file_id="aud_1", raw_path=raw)
    enqueue_task(config=config, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1")

    result = process_once(config=config, run_id="run_1", vad=MockVADAdapter(), asr=_FakeDiarizedASR(), llm=MockLLMAdapter())
    assert result.task_type == "transcribe_diarize" and result.status == "succeeded"

    extract_tasks = _tasks_by_type(config, "extract_features")
    assert len(extract_tasks) == 1
    assert extract_tasks[0]["target_type"] == "audio_file"
    assert extract_tasks[0]["target_id"] == "aud_1"
    assert extract_tasks[0]["status"] == "pending"
    # The sibling session_derive edge (same upstream) fans out as before.
    assert len(_tasks_by_type(config, "session_derive")) == 1


def test_extract_features_fan_out_gated_by_config_without_hurting_siblings(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize",
        pipeline_auto_extract_features=False,
    )
    raw = tmp_path / "raw" / "a.wav"
    _write_pcm_wav(raw)
    _seed_audio_file(config, audio_file_id="aud_1", raw_path=raw)
    enqueue_task(config=config, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1")

    result = process_once(config=config, run_id="run_1", vad=MockVADAdapter(), asr=_FakeDiarizedASR(), llm=MockLLMAdapter())
    assert result.status == "succeeded"

    # The gate suppresses ONLY the extraction leaf; the shared-upstream session_derive edge
    # must be unaffected (regression guard for filtering by downstream, not upstream, type).
    assert _tasks_by_type(config, "extract_features") == []
    assert len(_tasks_by_type(config, "session_derive")) == 1


def test_asr_chunk_edge_resolves_parent_audio_file(tmp_path: Path) -> None:
    from personal_context_node.process_runner import _audio_file_ids_for_chunk_in_conn

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw = tmp_path / "raw" / "a.wav"
    _write_pcm_wav(raw)
    _seed_audio_file(config, audio_file_id="aud_1", raw_path=raw)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into audio_chunks (chunk_id, audio_file_id, source_start_ms, source_end_ms, local_chunk_path, status) values ('chunk_1', 'aud_1', 0, 1000, '/work/chunk_1.wav', 'pending_asr')"
        )
        conn.commit()
        assert _audio_file_ids_for_chunk_in_conn(conn, chunk_id="chunk_1") == ["aud_1"]
        assert _audio_file_ids_for_chunk_in_conn(conn, chunk_id="chunk_unknown") == []
    finally:
        conn.close()


def test_extract_features_task_writes_both_artifacts_and_is_idempotent(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize")
    raw = tmp_path / "raw" / "a.wav"
    _write_pcm_wav(raw)
    _seed_audio_file(config, audio_file_id="aud_1", raw_path=raw)
    enqueue_task(config=config, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1")

    # transcribe first (writes the segments), which fans out the extract_features task.
    embed = _FakeEmbedAdapter()
    emotion = _FakeEmotionAdapter()
    first = process_once(
        config=config, run_id="run_1", vad=MockVADAdapter(), asr=_FakeDiarizedASR(),
        llm=MockLLMAdapter(), embed=embed, emotion=emotion,
    )
    assert first.task_type == "transcribe_diarize"

    # No session_derive fan-in yet may claim before extract_features in PROCESS_TASK_ORDER --
    # claim types explicitly so this test exercises exactly the extraction handler.
    second = process_once(
        config=config, run_id="run_2", vad=MockVADAdapter(), asr=_FakeDiarizedASR(),
        llm=MockLLMAdapter(), embed=embed, emotion=emotion, task_types=("extract_features",),
    )
    assert second.task_type == "extract_features"
    assert second.status == "succeeded"

    conn = connect(config.database_path)
    try:
        segment_ids = [str(r["segment_id"]) for r in fetch_all(conn, "select segment_id from transcript_segments where audio_file_id = 'aud_1' and is_active = 1")]
    finally:
        conn.close()
    assert len(segment_ids) == 2
    assert set(get_embeddings(config=config, segment_ids=segment_ids)) == set(segment_ids)
    assert set(get_emotions(config=config, segment_ids=segment_ids)) == set(segment_ids)
    assert embed.batch_calls, "the batched path must be used when the adapter exposes embed_batch"

    # A leaf: succeeding must enqueue NOTHING downstream of extract_features.
    types_now = {r["task_type"] for r in process_status_rows(config=config)}
    assert types_now == {"transcribe_diarize", "extract_features", "session_derive"}

    # Idempotent re-run: nothing pending for the file anymore -> fast no-op success.
    from personal_context_node.tasks import rerun_task

    rerun_task(config=config, task_type="extract_features", target_type="audio_file", target_id="aud_1")
    third = process_once(
        config=config, run_id="run_3", vad=MockVADAdapter(), asr=_FakeDiarizedASR(),
        llm=MockLLMAdapter(), embed=embed, emotion=emotion, task_types=("extract_features",),
    )
    assert third.status == "succeeded"
    assert len(embed.batch_calls) == 1  # no pending segments -> no second wire call


def test_worker_feature_adapters_are_resident_and_closeable(tmp_path: Path) -> None:
    from personal_context_node.web.worker import PipelineWorker

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    worker = PipelineWorker(config=config)

    built = {"embed": 0, "emotion": 0}
    closed = {"n": 0}

    class _Stub:
        def close(self):
            closed["n"] += 1

    def embed_factory():
        built["embed"] += 1
        return _Stub()

    def emotion_factory():
        built["emotion"] += 1
        return _Stub()

    worker._embed_factory = embed_factory
    worker._emotion_factory = emotion_factory

    pair1 = worker._resident_feature_adapters(config)
    pair2 = worker._resident_feature_adapters(config)
    assert pair1 is pair2  # cached across drains for an unchanged effective config
    assert built == {"embed": 1, "emotion": 1}

    worker.close_adapters()  # shutdown path releases the feature pair too
    assert closed["n"] == 2
    assert worker._feature_adapters is None

    # Next drain rebuilds fresh.
    worker._resident_feature_adapters(config)
    assert built == {"embed": 2, "emotion": 2}
    worker.close_feature_adapters()
    assert closed["n"] == 4
