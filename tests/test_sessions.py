from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_derive_sessions_splits_by_gap_and_reuses_existing_ids(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)

    first = derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    assert first.sessions_derived == 2
    rows = _session_rows(config.database_path)
    assert [row["segment_count"] for row in rows] == [1, 1]
    first_ids = [row["session_id"] for row in rows]

    second = derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    assert second.sessions_derived == 2
    assert [row["session_id"] for row in _session_rows(config.database_path)] == first_ids


def test_derive_sessions_sets_primary_person_from_segment_attribution(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute(
            """
            insert into persons (person_id, display_name, person_type, created_at, updated_at)
            values (?, ?, ?, ?, ?)
            """,
            ("person_paul", "Paul", "human", "2087-05-10T00:00:00Z", "2087-05-10T00:00:00Z"),
        )
        conn.execute(
            """
            insert into speaker_mappings (
              speaker, person_label, updated_at, speaker_cluster_id, person_id
            ) values (?, ?, ?, ?, ?)
            """,
            ("self", "Paul", "2087-05-10T00:00:00Z", "self", "person_paul"),
        )
        conn.commit()
    finally:
        conn.close()

    derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select primary_person_id from sessions order by started_at")
    finally:
        conn.close()

    assert rows == [{"primary_person_id": "person_paul"}, {"primary_person_id": "person_paul"}]


def _insert_audio_and_segments(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:test",
                2_000_000,
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
                "imported",
            ),
        )
        for segment_id, start_ms, end_ms in [
            ("seg_early", 0, 10_000),
            ("seg_late", 30 * 60 * 1000, 30 * 60 * 1000 + 10_000),
        ]:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_test",
                    f"chk_{segment_id}",
                    start_ms,
                    end_ms,
                    "测试片段",
                    "zh",
                    "self",
                    f"ev_{segment_id}",
                    0.99,
                    "MockASRAdapter",
                    "mock-asr",
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _session_rows(database_path: Path) -> list[dict[str, object]]:
    conn = connect(database_path)
    try:
        return fetch_all(conn, "select session_id, segment_count from sessions order by started_at")
    finally:
        conn.close()
