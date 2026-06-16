from __future__ import annotations

from pathlib import Path

import numpy as np

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import (
    get_embeddings,
    pending_embedding_segment_ids,
    put_embedding,
    put_embeddings_bulk,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_put_get_roundtrip(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])

    vector = list(range(192))  # 192-dim
    put_embedding(config=config, segment_id="seg_1", vector=vector)

    result = get_embeddings(config=config, segment_ids=["seg_1"])
    assert set(result) == {"seg_1"}
    got = result["seg_1"]
    assert isinstance(got, np.ndarray)
    assert got.dtype == np.float32
    assert got.shape == (192,)
    np.testing.assert_allclose(got, np.asarray(vector, dtype=np.float32), atol=1e-5)

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select dim from segment_embeddings where segment_id = 'seg_1'")
    finally:
        conn.close()
    assert rows == [{"dim": 192}]


def test_put_embeddings_bulk(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    items = [
        ("seg_1", [0.1, 0.2, 0.3]),
        ("seg_2", [1.0, 2.0, 3.0]),
        ("seg_3", [4.0, 5.0, 6.0]),
    ]
    assert put_embeddings_bulk(config=config, items=items) == 3

    result = get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])
    assert set(result) == {"seg_1", "seg_2", "seg_3"}
    np.testing.assert_allclose(result["seg_2"], np.asarray([1.0, 2.0, 3.0], dtype=np.float32), atol=1e-5)


def test_get_embeddings_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    assert get_embeddings(config=config, segment_ids=[]) == {}


def test_pending_lists_active_without_embedding(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    # No embeddings yet -> all three pending, ordered.
    assert pending_embedding_segment_ids(config=config) == ["seg_1", "seg_2", "seg_3"]

    put_embedding(config=config, segment_id="seg_1", vector=[0.0, 1.0, 2.0])
    assert pending_embedding_segment_ids(config=config) == ["seg_2", "seg_3"]

    put_embeddings_bulk(config=config, items=[("seg_2", [0.0]), ("seg_3", [1.0])])
    assert pending_embedding_segment_ids(config=config) == []

    # session_id scoping: only the matching session's pending segments are returned.
    _insert_session_with_segments(
        config.database_path, ["seg_o1", "seg_o2"], session_id="ses_other", audio_file_id="aud_other"
    )
    assert pending_embedding_segment_ids(config=config, session_id="ses_other") == ["seg_o1", "seg_o2"]
    assert pending_embedding_segment_ids(config=config, session_id="ses_test") == []


def _insert_session_with_segments(
    database_path: Path,
    segment_ids: list[str],
    *,
    session_id: str = "ses_test",
    audio_file_id: str = "aud_test",
    date_key: str = "2087-05-10",
) -> None:
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
