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


def test_derive_sessions_preserves_exclude_from_memory_when_reusing_session_id(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)

    derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)
    conn = connect(config.database_path)
    try:
        first_session_id = fetch_all(conn, "select session_id from sessions order by started_at")[0]["session_id"]
        conn.execute("update sessions set exclude_from_memory = 1 where session_id = ?", (first_session_id,))
        conn.commit()
    finally:
        conn.close()

    derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            "select session_id, exclude_from_memory from sessions order by started_at",
        )
    finally:
        conn.close()

    assert rows[0] == {"session_id": first_session_id, "exclude_from_memory": 1}
    assert rows[1]["exclude_from_memory"] == 0


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


def test_derive_sessions_clears_inactive_segment_session_assignments_on_rebuild(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)

    derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)
    conn = connect(config.database_path)
    try:
        conn.execute("update transcript_segments set is_active = 0 where segment_id = 'seg_early'")
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version, is_active
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_early_rerun",
                "aud_test",
                "chk_seg_early_rerun",
                0,
                10_000,
                "重跑片段",
                "zh",
                "self",
                "ev_seg_early_rerun",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
                1,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select segment_id, is_active, session_id
            from transcript_segments
            where segment_id in ('seg_early', 'seg_early_rerun')
            order by segment_id
            """,
        )
    finally:
        conn.close()

    assert rows[0] == {"segment_id": "seg_early", "is_active": 0, "session_id": None}
    assert rows[1]["segment_id"] == "seg_early_rerun"
    assert rows[1]["is_active"] == 1
    assert rows[1]["session_id"]


def test_derive_sessions_splits_segments_by_absolute_gap_across_audio_files(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_two_audio_files_with_absolute_gap(config.database_path)

    result = derive_sessions_for_day(config=config, day="2087-05-10", session_gap_minutes=20)

    assert result.sessions_derived == 2
    rows = _session_rows(config.database_path)
    assert [row["segment_count"] for row in rows] == [1, 1]


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


def _insert_two_audio_files_with_absolute_gap(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        for audio_file_id, recorded_at, segment_id in [
            ("aud_early", "2087-05-10T08:00:00+08:00", "seg_early"),
            ("aud_late", "2087-05-10T09:00:00+08:00", "seg_late"),
        ]:
            conn.execute(
                """
                insert into audio_files (
                  audio_file_id, source_device, source_path, local_raw_path, sha256,
                  duration_ms, recorded_at, imported_at, status
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audio_file_id,
                    "DJI Mic 3",
                    f"/source/{audio_file_id}.wav",
                    f"/local/{audio_file_id}.wav",
                    f"sha256:{audio_file_id}",
                    60_000,
                    recorded_at,
                    "2087-05-10T10:00:00+08:00",
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
                    segment_id,
                    audio_file_id,
                    f"chk_{segment_id}",
                    0,
                    10_000,
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
