from __future__ import annotations

from personal_context_node.storage.sqlite import _run_migrations, connect, fetch_all, initialize


def test_initialize_records_schema_migration_version(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        migrations = fetch_all(conn, "select version, name from schema_migrations order by version")
    finally:
        conn.close()

    assert migrations == [{"version": 1, "name": "base_schema"}]


def test_initialize_does_not_duplicate_schema_migration_rows(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        initialize(conn)

        migrations = fetch_all(conn, "select version, name from schema_migrations order by version")
    finally:
        conn.close()

    assert migrations == [{"version": 1, "name": "base_schema"}]


def test_initialize_audio_files_indexes_source_snapshot_identity_time_and_status(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        indexes = fetch_all(conn, "pragma index_list(audio_files)")
        source_identity = fetch_all(conn, "pragma index_info(idx_audio_files_source_identity)")
        recorded_at = fetch_all(conn, "pragma index_info(idx_audio_files_recorded_at)")
        status = fetch_all(conn, "pragma index_info(idx_audio_files_status)")
    finally:
        conn.close()

    index_names = {row["name"] for row in indexes}
    assert "idx_audio_files_source_identity" in index_names
    assert "idx_audio_files_recorded_at" in index_names
    assert "idx_audio_files_status" in index_names
    assert [row["name"] for row in source_identity] == [
        "source_device",
        "source_path",
        "source_size_bytes",
        "source_mtime_ns",
        "sha256",
    ]
    assert [row["name"] for row in recorded_at] == ["recorded_at"]
    assert [row["name"] for row in status] == ["status"]


def test_initialize_tasks_schema_tracks_claim_priority_and_retry_metadata(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(tasks)")
        indexes = fetch_all(conn, "pragma index_list(tasks)")
        claim_index = fetch_all(conn, "pragma index_info(idx_tasks_claim)")
        target_index = fetch_all(conn, "pragma index_info(idx_tasks_target)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["priority"]["type"].lower() == "integer"
    assert column_by_name["retry_count"]["type"].lower() == "integer"
    assert column_by_name["max_retries"]["type"].lower() == "integer"
    assert column_by_name["available_at"]["type"].lower() == "text"
    assert column_by_name["lease_expires_at"]["type"].lower() == "text"
    assert column_by_name["updated_at"]["type"].lower() == "text"
    index_names = {row["name"] for row in indexes}
    assert "idx_tasks_claim" in index_names
    assert "idx_tasks_target" in index_names
    # Index column order mirrors claim_next_task's filter + "order by priority, available_at".
    assert [row["name"] for row in claim_index] == ["task_type", "status", "priority", "available_at"]
    assert [row["name"] for row in target_index] == ["target_type", "target_id"]


def test_initialize_replaces_legacy_claim_index_definition(tmp_path) -> None:
    # An existing DB may carry the legacy idx_tasks_claim (status, available_at, priority). Because
    # "create index if not exists" is a no-op when the name already exists, _run_migrations must
    # drop then recreate it so the index matches the priority-first claim order. Simulate the legacy
    # DB by initializing fully, downgrading only the index, then re-running migrations (as a fresh
    # process would — initialize() itself caches per-process, so call _run_migrations directly).
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        conn.execute("drop index idx_tasks_claim")
        conn.execute("create index idx_tasks_claim on tasks(status, available_at, priority)")
        conn.commit()
        assert [row["name"] for row in fetch_all(conn, "pragma index_info(idx_tasks_claim)")] == [
            "status", "available_at", "priority",
        ]

        _run_migrations(conn)  # a fresh process re-runs migrations; must converge the legacy index

        claim_index = fetch_all(conn, "pragma index_info(idx_tasks_claim)")
    finally:
        conn.close()

    assert [row["name"] for row in claim_index] == ["task_type", "status", "priority", "available_at"]


def test_initialize_memory_candidates_schema_tracks_prompt_version(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_candidates)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["prompt_version"]["type"].lower() == "text"
    assert column_by_name["prompt_version"]["notnull"] == 1


def test_initialize_sessions_schema_tracks_primary_person_and_date_index(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(sessions)")
        indexes = fetch_all(conn, "pragma index_list(sessions)")
        index_columns = fetch_all(conn, "pragma index_info(idx_sessions_date)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["primary_person_id"]["type"].lower() == "text"
    index_names = {row["name"] for row in indexes}
    assert "idx_sessions_date" in index_names
    assert [row["name"] for row in index_columns] == ["date_key", "started_at"]


def test_initialize_sessions_has_nullable_name_column(tmp_path) -> None:
    # The 声纹/审核 UI surfaces a user-chosen session name; add a nullable `name` column.
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(sessions)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert "name" in column_by_name
    assert column_by_name["name"]["type"].lower() == "text"
    assert column_by_name["name"]["notnull"] == 0  # nullable
    assert column_by_name["name"]["dflt_value"] is None


def test_initialize_sessions_name_is_idempotent(tmp_path) -> None:
    # Re-running migrations on a DB that ALREADY has the `name` column must not error or change it
    # (initialize() caches per-process, so call _run_migrations directly as a fresh process would).
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        before = fetch_all(conn, "pragma table_info(sessions)")

        _run_migrations(conn)  # second pass over an already-migrated table

        after = fetch_all(conn, "pragma table_info(sessions)")
    finally:
        conn.close()

    assert before == after
    assert "name" in {row["name"] for row in after}


def test_initialize_speaker_mappings_schema_tracks_design_metadata(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(speaker_mappings)")
        indexes = fetch_all(conn, "pragma index_list(speaker_mappings)")
        cluster_index = fetch_all(conn, "pragma index_info(idx_speaker_mappings_cluster)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["speaker_mapping_id"]["type"].lower() == "text"
    assert column_by_name["confidence"]["type"].lower() == "real"
    assert column_by_name["source"]["type"].lower() == "text"
    assert column_by_name["created_at"]["type"].lower() == "text"
    index_names = {row["name"] for row in indexes}
    assert "idx_speaker_mappings_cluster" in index_names
    assert [row["name"] for row in cluster_index] == ["speaker_cluster_id"]


def test_initialize_transcript_segments_schema_tracks_absolute_time_and_indexes(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(transcript_segments)")
        indexes = fetch_all(conn, "pragma index_list(transcript_segments)")
        session_time = fetch_all(conn, "pragma index_info(idx_segments_session_time)")
        audio_time = fetch_all(conn, "pragma index_info(idx_segments_audio_time)")
        cluster = fetch_all(conn, "pragma index_info(idx_segments_cluster)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["absolute_start_at"]["type"].lower() == "text"
    assert column_by_name["absolute_end_at"]["type"].lower() == "text"
    assert column_by_name["speaker_cluster_id"]["type"].lower() == "text"
    assert column_by_name["decode_config_json"]["type"].lower() == "text"
    assert column_by_name["asr_tags_json"]["type"].lower() == "text"
    index_names = {row["name"] for row in indexes}
    assert "idx_segments_session_time" in index_names
    assert "idx_segments_audio_time" in index_names
    assert "idx_segments_cluster" in index_names
    assert [row["name"] for row in session_time] == ["session_id", "absolute_start_at"]
    assert [row["name"] for row in audio_time] == ["audio_file_id", "start_ms", "end_ms"]
    assert [row["name"] for row in cluster] == ["speaker_cluster_id"]


def test_initialize_audio_chunks_schema_tracks_work_path_absolute_time_and_index(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(audio_chunks)")
        indexes = fetch_all(conn, "pragma index_list(audio_chunks)")
        audio_time = fetch_all(conn, "pragma index_info(idx_chunks_audio_time)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["local_work_path"]["type"].lower() == "text"
    assert column_by_name["start_ms"]["type"].lower() == "integer"
    assert column_by_name["end_ms"]["type"].lower() == "integer"
    assert column_by_name["absolute_start_at"]["type"].lower() == "text"
    assert column_by_name["absolute_end_at"]["type"].lower() == "text"
    assert column_by_name["vad_backend"]["type"].lower() == "text"
    assert column_by_name["vad_config_json"]["type"].lower() == "text"
    assert column_by_name["created_at"]["type"].lower() == "text"
    index_names = {row["name"] for row in indexes}
    assert "idx_chunks_audio_time" in index_names
    assert [row["name"] for row in audio_time] == ["audio_file_id", "start_ms", "end_ms"]


def test_initialize_does_not_persist_speech_ranges_table(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        tables = fetch_all(
            conn,
            "select name from sqlite_master where type = 'table' and name = 'speech_ranges'",
        )
    finally:
        conn.close()

    assert tables == []


def test_initialize_memory_cards_schema_includes_source_type(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["source_type"]["notnull"] == 1
    assert column_by_name["source_type"]["dflt_value"] == "'confirmed_generated'"


def test_initialize_evidence_refs_schema_includes_design_columns(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(evidence_refs)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["source_ref"]["type"].lower() == "text"
    assert column_by_name["source_ref"]["notnull"] == 1
    assert column_by_name["owner_id"]["type"].lower() == "text"
    assert column_by_name["summary"]["type"].lower() == "text"
    assert column_by_name["created_at"]["type"].lower() == "text"
    assert column_by_name["created_at"]["notnull"] == 1


def test_initialize_evidence_refs_enforces_unique_source_ref(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        conn.execute(
            """
            insert into evidence_refs (
              evidence_id, source_type, source_ref, source_id, quote, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            ("ev_unique_1", "transcript_segment", "seg_unique", "seg_unique", "quote 1", "2087-05-10T00:00:00Z"),
        )

        try:
            conn.execute(
                """
                insert into evidence_refs (
                  evidence_id, source_type, source_ref, source_id, quote, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                ("ev_unique_2", "transcript_segment", "seg_unique", "seg_unique", "quote 2", "2087-05-10T00:01:00Z"),
            )
        except Exception as exc:
            error = exc
        else:
            error = None
    finally:
        conn.close()

    assert type(error).__name__ == "IntegrityError"


def test_initialize_signed_events_trust_status_defaults_unverified(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(signed_events)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["trust_status"]["dflt_value"] == "'unverified'"


def test_initialize_signed_events_indexes_object_versions(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        indexes = fetch_all(conn, "pragma index_list(signed_events)")
        index_columns = fetch_all(conn, "pragma index_info(idx_signed_events_object)")
    finally:
        conn.close()

    index_names = {row["name"] for row in indexes}
    assert "idx_signed_events_object" in index_names
    assert [row["name"] for row in index_columns] == ["object_id", "object_version"]


def test_initialize_daily_reports_schema_uses_date_key_and_metrics(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(daily_reports)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["date_key"]["pk"] == 1
    assert column_by_name["status"]["notnull"] == 1
    assert column_by_name["note_path"]["type"].lower() == "text"
    assert column_by_name["total_recorded_ms"]["dflt_value"] == "0"
    assert column_by_name["active_speech_ms"]["dflt_value"] == "0"
    assert column_by_name["self_speech_ms"]["dflt_value"] == "0"
    assert column_by_name["others_speech_ms"]["dflt_value"] == "0"
    assert column_by_name["generated_at"]["type"].lower() == "text"
    assert column_by_name["reviewed_at"]["type"].lower() == "text"
    assert column_by_name["error"]["type"].lower() == "text"
    assert column_by_name["created_at"]["notnull"] == 1
    assert column_by_name["updated_at"]["notnull"] == 1


def test_initialize_archive_records_schema_tracks_targets_and_status(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(archive_records)")
        indexes = fetch_all(conn, "pragma index_list(archive_records)")
        index_columns = fetch_all(conn, "pragma index_info(idx_archive_records_target_archive)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["target_type"]["type"].lower() == "text"
    assert column_by_name["target_type"]["notnull"] == 1
    assert column_by_name["target_id"]["type"].lower() == "text"
    assert column_by_name["target_id"]["notnull"] == 1
    assert column_by_name["audio_file_id"]["notnull"] == 0
    assert column_by_name["status"]["notnull"] == 1
    assert column_by_name["last_error"]["type"].lower() == "text"
    assert column_by_name["created_at"]["notnull"] == 1
    assert column_by_name["updated_at"]["notnull"] == 1
    index_names = {row["name"] for row in indexes}
    assert "idx_archive_records_target_archive" in index_names
    assert [row["name"] for row in index_columns] == ["target_type", "target_id", "archive_path"]


def test_initialize_memory_candidates_schema_tracks_review_lifecycle(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_candidates)")
        indexes = fetch_all(conn, "pragma index_list(memory_candidates)")
        index_columns = fetch_all(conn, "pragma index_info(idx_candidates_status)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["source_type"]["notnull"] == 1
    assert column_by_name["edited_claim"]["type"].lower() == "text"
    assert column_by_name["review_note_path"]["type"].lower() == "text"
    assert column_by_name["reviewed_at"]["type"].lower() == "text"
    assert column_by_name["created_card_id"]["type"].lower() == "text"
    assert column_by_name["created_at"]["notnull"] == 1
    assert column_by_name["updated_at"]["notnull"] == 1
    index_names = {row["name"] for row in indexes}
    assert "idx_candidates_status" in index_names
    assert [row["name"] for row in index_columns] == ["status"]


def test_initialize_migrates_legacy_daily_reports_day_to_date_key(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        conn.execute(
            """
            create table daily_reports (
              day text primary key,
              status text not null,
              updated_at text not null,
              error text
            )
            """
        )
        conn.execute(
            "insert into daily_reports (day, status, updated_at, error) values (?, ?, ?, ?)",
            ("2087-05-10", "review_pending", "2087-05-10T00:00:00Z", None),
        )
        initialize(conn)

        rows = fetch_all(conn, "select date_key, status, created_at, updated_at from daily_reports")
    finally:
        conn.close()

    assert rows == [
        {
            "date_key": "2087-05-10",
            "status": "review_pending",
            "created_at": "",
            "updated_at": "2087-05-10T00:00:00Z",
        }
    ]


def test_initialize_memory_cards_schema_includes_current_version(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["current_version"]["type"].lower() == "integer"
    assert column_by_name["current_version"]["notnull"] == 1
    assert column_by_name["current_version"]["dflt_value"] == "1"


def test_initialize_memory_cards_schema_includes_owner_id(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["owner_id"]["type"].lower() == "text"
    assert column_by_name["owner_id"]["notnull"] == 1


def test_initialize_migrates_legacy_memory_cards_owner_id_before_owner_index(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        conn.execute(
            """
            create table memory_cards (
              card_id text primary key,
              current_version integer not null default 1,
              owner_did text not null,
              claim_type text not null,
              claim text not null,
              source_type text not null default 'confirmed_generated',
              subject_json text not null,
              evidence_refs_json text not null,
              visibility_json text not null default '{"type":"private"}',
              tags_json text not null default '[]',
              status text not null,
              source_event_hash text not null,
              created_at text not null,
              updated_at text not null default ''
            )
            """
        )
        conn.execute(
            """
            insert into memory_cards (
              card_id, owner_did, claim_type, claim, subject_json, evidence_refs_json,
              status, source_event_hash, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mem_legacy_owner",
                "did:key:legacy-owner",
                "requirement",
                "Legacy cards migrate owner_id before indexing.",
                '{"type":"project","id":"pcn"}',
                "[]",
                "active",
                "sha256:legacy",
                "2087-05-10T00:00:00Z",
            ),
        )
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
        indexes = fetch_all(conn, "pragma index_list(memory_cards)")
        owner_index = fetch_all(conn, "pragma index_info(idx_memory_cards_owner)")
        subject_index = fetch_all(conn, "pragma index_info(idx_memory_cards_subject)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    index_names = {row["name"] for row in indexes}
    assert column_by_name["owner_id"]["notnull"] == 1
    assert "idx_memory_cards_owner" in index_names
    assert "idx_memory_cards_subject" in index_names
    assert [row["name"] for row in owner_index] == ["owner_id", "status"]
    assert [row["name"] for row in subject_index] == ["claim_type", "status"]


def test_initialize_memory_cards_schema_includes_confidence(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["confidence"]["type"].lower() == "real"
    assert column_by_name["confidence"]["notnull"] == 0


def test_initialize_memory_cards_schema_includes_temporal_bounds(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["observed_at"]["type"].lower() == "text"
    assert column_by_name["valid_from"]["type"].lower() == "text"
    assert column_by_name["valid_until"]["type"].lower() == "text"


def test_initialize_segment_person_overrides_has_source_defaulting_manual(tmp_path) -> None:
    # The supervised-identity split needs a `source` column distinguishing user ground truth
    # ('manual') from auto-inferred guesses ('voiceprint'). Existing rows must default to 'manual'.
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(segment_person_overrides)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert "source" in column_by_name
    assert column_by_name["source"]["type"].lower() == "text"
    assert column_by_name["source"]["notnull"] == 1
    assert column_by_name["source"]["dflt_value"] == "'manual'"


def test_initialize_segment_person_overrides_source_backfills_existing_rows(tmp_path) -> None:
    # A row written by a legacy schema (no `source` column) is treated as ground truth: after the
    # add-column migration it reads back as source='manual', so enrollment works immediately.
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        conn.execute(
            "create table segment_person_overrides ("
            "  segment_id text primary key,"
            "  person_label text not null,"
            "  updated_at text not null,"
            "  person_id text"
            ")"
        )
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id) "
            "values (?, ?, ?, ?)",
            ("seg_legacy", "Alice", "2087-05-10T00:00:00Z", "per_a"),
        )
        initialize(conn)

        rows = fetch_all(conn, "select segment_id, source from segment_person_overrides")
    finally:
        conn.close()

    assert rows == [{"segment_id": "seg_legacy", "source": "manual"}]


def test_initialize_segment_person_overrides_source_is_idempotent(tmp_path) -> None:
    # Re-running migrations on a DB that ALREADY has the `source` column must not error or change it
    # (initialize() caches per-process, so call _run_migrations directly as a fresh process would).
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        before = fetch_all(conn, "pragma table_info(segment_person_overrides)")

        _run_migrations(conn)  # second pass over an already-migrated table

        after = fetch_all(conn, "pragma table_info(segment_person_overrides)")
    finally:
        conn.close()

    assert before == after
    column_by_name = {row["name"]: row for row in after}
    assert "source" in column_by_name
