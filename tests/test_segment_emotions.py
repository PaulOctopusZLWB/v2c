from __future__ import annotations

from pathlib import Path

from personal_context_node import transcription
from personal_context_node.config import AppConfig
from personal_context_node.segment_emotions import (
    emotion_distribution,
    emotion_labels_for_scope,
    extract_pending_emotions,
    get_emotions,
    pending_emotion_segment_ids,
    put_emotions_bulk,
)
from personal_context_node.speaker_review import upsert_segment_person_override
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_put_get_roundtrip(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])

    item = ("seg_1", {"label": "中立/neutral", "scores": {"中立/neutral": 0.7, "开心/happy": 0.3}})
    assert put_emotions_bulk(config=config, items=[item]) == 1

    result = get_emotions(config=config, segment_ids=["seg_1"])
    assert set(result) == {"seg_1"}
    got = result["seg_1"]
    assert got["label"] == "中立/neutral"
    assert got["scores"] == {"中立/neutral": 0.7, "开心/happy": 0.3}

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select label from segment_emotions where segment_id = 'seg_1'")
    finally:
        conn.close()
    assert rows == [{"label": "中立/neutral"}]


def test_put_emotions_bulk(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    items = [
        ("seg_1", {"label": "开心/happy", "scores": {"开心/happy": 0.9}}),
        ("seg_2", {"label": "难过/sad", "scores": {"难过/sad": 0.6, "中立/neutral": 0.4}}),
        ("seg_3", {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}),
    ]
    assert put_emotions_bulk(config=config, items=items) == 3

    result = get_emotions(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])
    assert set(result) == {"seg_1", "seg_2", "seg_3"}
    assert result["seg_2"]["label"] == "难过/sad"
    assert result["seg_2"]["scores"] == {"难过/sad": 0.6, "中立/neutral": 0.4}


def test_put_emotions_bulk_upserts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])

    put_emotions_bulk(config=config, items=[("seg_1", {"label": "开心/happy", "scores": {"开心/happy": 0.9}})])
    put_emotions_bulk(config=config, items=[("seg_1", {"label": "难过/sad", "scores": {"难过/sad": 0.8}})])

    got = get_emotions(config=config, segment_ids=["seg_1"])["seg_1"]
    assert got["label"] == "难过/sad"
    assert got["scores"] == {"难过/sad": 0.8}


def test_get_emotions_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    assert get_emotions(config=config, segment_ids=[]) == {}


def test_get_emotions_chunks_large_input(tmp_path: Path) -> None:
    # >999 ids must not trip SQLite's per-statement bind-variable limit.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    ids = [f"seg_{i:04d}" for i in range(1200)]
    _insert_session_with_segments(config.database_path, ids)
    put_emotions_bulk(
        config=config, items=[(sid, {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}) for sid in ids]
    )

    got = get_emotions(config=config, segment_ids=ids)
    assert len(got) == 1200


def test_pending_lists_active_without_emotion(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    # No emotions yet -> all three pending, ordered.
    assert pending_emotion_segment_ids(config=config) == ["seg_1", "seg_2", "seg_3"]

    put_emotions_bulk(config=config, items=[("seg_1", {"label": "开心/happy", "scores": {"开心/happy": 1.0}})])
    assert pending_emotion_segment_ids(config=config) == ["seg_2", "seg_3"]

    put_emotions_bulk(
        config=config,
        items=[
            ("seg_2", {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}),
            ("seg_3", {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}),
        ],
    )
    assert pending_emotion_segment_ids(config=config) == []

    # session_id scoping: only the matching session's pending segments are returned.
    _insert_session_with_segments(
        config.database_path, ["seg_o1", "seg_o2"], session_id="ses_other", audio_file_id="aud_other"
    )
    assert pending_emotion_segment_ids(config=config, session_id="ses_other") == ["seg_o1", "seg_o2"]
    assert pending_emotion_segment_ids(config=config, session_id="ses_test") == []


def test_extract_emotes_all(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    monkeypatch.setattr(
        transcription,
        "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    result = extract_pending_emotions(config=config, classify_fn=classify_fn)
    assert result == {"emoted": 3, "skipped_missing_audio": 0, "failed": 0, "total": 3}

    stored = get_emotions(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])
    assert set(stored) == {"seg_1", "seg_2", "seg_3"}

    # A second pass has nothing left to classify.
    second = extract_pending_emotions(config=config, classify_fn=classify_fn)
    assert second == {"emoted": 0, "skipped_missing_audio": 0, "failed": 0, "total": 0}


def test_extract_skips_missing_audio(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    def fake_path(*, config, segment_id):
        if segment_id == "seg_2":
            return None
        return Path(f"/slices/{segment_id}.wav")

    monkeypatch.setattr(transcription, "segment_audio_path", fake_path)
    classify_fn = lambda path: {"label": "开心/happy", "scores": {"开心/happy": 1.0}}

    result = extract_pending_emotions(config=config, classify_fn=classify_fn)
    assert result == {"emoted": 2, "skipped_missing_audio": 1, "failed": 0, "total": 3}

    # The skipped segment stays pending; the emoted ones do not.
    assert pending_emotion_segment_ids(config=config) == ["seg_2"]


def test_extract_reports_progress(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    monkeypatch.setattr(
        transcription,
        "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    calls: list[tuple[int, int]] = []
    extract_pending_emotions(
        config=config, classify_fn=classify_fn, progress=lambda done, total: calls.append((done, total))
    )

    assert len(calls) == 3
    assert calls[-1] == (3, 3)


def test_extract_scoped_by_session(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2"])
    _insert_session_with_segments(
        config.database_path, ["seg_o1", "seg_o2"], session_id="ses_other", audio_file_id="aud_other"
    )

    monkeypatch.setattr(
        transcription,
        "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    result = extract_pending_emotions(config=config, classify_fn=classify_fn, session_id="ses_other")
    assert result == {"emoted": 2, "skipped_missing_audio": 0, "failed": 0, "total": 2}

    stored = get_emotions(config=config, segment_ids=["seg_1", "seg_2", "seg_o1", "seg_o2"])
    assert set(stored) == {"seg_o1", "seg_o2"}
    assert pending_emotion_segment_ids(config=config, session_id="ses_test") == ["seg_1", "seg_2"]


def test_extract_continues_past_failed_classify(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])
    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )

    def classify_fn(path: str) -> dict:
        if "seg_2" in path:
            raise RuntimeError("emotion2vec failed on this slice")
        return {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    result = extract_pending_emotions(config=config, classify_fn=classify_fn)
    assert result == {"emoted": 2, "skipped_missing_audio": 0, "failed": 1, "total": 3}
    assert set(get_emotions(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])) == {"seg_1", "seg_3"}
    assert pending_emotion_segment_ids(config=config) == ["seg_2"]  # the failed one stays pending


def test_emotion_distribution_overall_and_per_speaker(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    # Two speakers: spk_a (3 segs) + spk_b (2 segs); one active seg has no emotion row.
    _insert_session_with_segments(
        config.database_path,
        ["s1", "s2", "s3", "s4", "s5", "s_none"],
        speakers=["spk_a", "spk_a", "spk_a", "spk_b", "spk_b", "spk_a"],
    )
    put_emotions_bulk(
        config=config,
        items=[
            ("s1", {"label": "开心/happy", "scores": {"开心/happy": 0.9}}),
            ("s2", {"label": "开心/happy", "scores": {"开心/happy": 0.8}}),
            ("s3", {"label": "难过/sad", "scores": {"难过/sad": 0.7}}),
            ("s4", {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}),
            ("s5", {"label": "开心/happy", "scores": {"开心/happy": 0.6}}),
        ],
    )

    dist = emotion_distribution(config=config)

    assert dist["n"] == 5  # s_none has no emotion row and is excluded
    assert dist["overall"] == {"开心/happy": 3, "难过/sad": 1, "中立/neutral": 1}

    per = {row["label"]: row for row in dist["per_speaker"]}
    assert [row["label"] for row in dist["per_speaker"]] == ["spk_a", "spk_b"]  # sorted by total desc
    assert per["spk_a"]["total"] == 3
    assert per["spk_a"]["emotions"] == {"开心/happy": 2, "难过/sad": 1}
    assert per["spk_a"]["dominant"] == "开心/happy"
    assert per["spk_b"]["total"] == 2
    assert per["spk_b"]["emotions"] == {"中立/neutral": 1, "开心/happy": 1}


def test_emotion_distribution_respects_person_override(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(
        config.database_path, ["s1", "s2"], speakers=["spk_a", "spk_b"]
    )
    put_emotions_bulk(
        config=config,
        items=[
            ("s1", {"label": "开心/happy", "scores": {"开心/happy": 0.9}}),
            ("s2", {"label": "难过/sad", "scores": {"难过/sad": 0.7}}),
        ],
    )
    # Relabel spk_b's segment to a named person — distribution should group it under that name.
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_x', '韩梅', 'contact', 0, '2087-05-10T08:00:00+08:00', '2087-05-10T08:00:00+08:00')"
        )
        upsert_segment_person_override(
            conn, segment_id="s2", person_id="per_x", person_label="韩梅", now="2087-05-10T08:00:00+08:00"
        )
        conn.commit()
    finally:
        conn.close()

    dist = emotion_distribution(config=config)
    labels = {row["label"] for row in dist["per_speaker"]}
    assert labels == {"spk_a", "韩梅"}  # override label replaces the raw speaker


def test_emotion_distribution_scoped_by_session(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["s1"], speakers=["spk_a"])
    _insert_session_with_segments(
        config.database_path, ["o1"], session_id="ses_other", audio_file_id="aud_other", speakers=["spk_z"]
    )
    put_emotions_bulk(
        config=config,
        items=[
            ("s1", {"label": "开心/happy", "scores": {"开心/happy": 1.0}}),
            ("o1", {"label": "难过/sad", "scores": {"难过/sad": 1.0}}),
        ],
    )

    scoped = emotion_distribution(config=config, session_id="ses_other")
    assert scoped["n"] == 1
    assert scoped["overall"] == {"难过/sad": 1}
    assert [r["label"] for r in scoped["per_speaker"]] == ["spk_z"]


def test_emotion_distribution_empty_scope(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    dist = emotion_distribution(config=config, session_id="ses_missing")
    assert dist == {"overall": {}, "per_speaker": [], "n": 0}


def test_emotion_labels_for_scope(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(
        config.database_path, ["s1", "s2", "s_none"], speakers=["spk_a", "spk_b", "spk_a"]
    )
    put_emotions_bulk(
        config=config,
        items=[
            ("s1", {"label": "开心/happy", "scores": {"开心/happy": 0.9}}),
            ("s2", {"label": "难过/sad", "scores": {"难过/sad": 0.7}}),
        ],
    )

    labels = emotion_labels_for_scope(config=config)
    assert labels == {"s1": "开心/happy", "s2": "难过/sad"}  # s_none excluded (no emotion row)


def _insert_session_with_segments(
    database_path: Path,
    segment_ids: list[str],
    *,
    session_id: str = "ses_test",
    audio_file_id: str = "aud_test",
    date_key: str = "2087-05-10",
    speakers: list[str] | None = None,
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
            speaker = speakers[index] if speakers is not None else "self"
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, audio_file_id, f"chk_{segment_id}", session_id, index * 1000, (index + 1) * 1000, f"{date_key}T08:00:{index:02d}.000000+08:00", f"{date_key}T08:00:{index + 1:02d}.000000+08:00", f"text {index + 1}", "zh", speaker, speaker, f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, f"{date_key}T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
