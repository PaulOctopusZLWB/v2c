from __future__ import annotations

from personal_context_node.storage.sqlite import _run_migrations, connect, fetch_all, initialize


def test_segment_embeddings_table_exists(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(segment_embeddings)")
        indexes = fetch_all(conn, "pragma index_list(segment_embeddings)")
        model_index = fetch_all(conn, "pragma index_info(idx_segment_embeddings_model)")

        # Re-running the DDL directly (as a fresh process would via _run_migrations)
        # must not raise; the per-process initialize() cache would otherwise hide a
        # non-idempotent create-table statement.
        _run_migrations(conn)
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert set(column_by_name) == {"segment_id", "model", "dim", "vector", "created_at"}
    assert column_by_name["segment_id"]["pk"] == 1
    assert column_by_name["segment_id"]["type"].lower() == "text"
    assert column_by_name["model"]["type"].lower() == "text"
    assert column_by_name["model"]["notnull"] == 1
    assert column_by_name["dim"]["type"].lower() == "integer"
    assert column_by_name["dim"]["notnull"] == 1
    assert column_by_name["vector"]["type"].lower() == "blob"
    assert column_by_name["vector"]["notnull"] == 1
    assert column_by_name["created_at"]["type"].lower() == "text"
    assert column_by_name["created_at"]["notnull"] == 1

    index_names = {row["name"] for row in indexes}
    assert "idx_segment_embeddings_model" in index_names
    assert [row["name"] for row in model_index] == ["model"]


def test_segment_embeddings_accepts_blob_row(tmp_path) -> None:
    import struct

    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        # FK is enforced (connect() runs `pragma foreign_keys = on`), and audio_files
        # has a NOT NULL FK from transcript_segments — insert both parents first.
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "af_emb",
                "device_a",
                "/src/a.wav",
                "/raw/a.wav",
                "sha256:a",
                1000,
                "2087-05-10T00:00:00Z",
                "2087-05-10T00:00:01Z",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("seg_emb", "af_emb", "chunk_emb", 0, 500, "hello", "en", "spk_0", "ev_emb"),
        )

        vector = struct.pack("<3f", 0.5, -1.25, 3.0)
        conn.execute(
            """
            insert into segment_embeddings (segment_id, model, dim, vector, created_at)
            values (?, ?, ?, ?, ?)
            """,
            ("seg_emb", "campplus_voxceleb", 3, vector, "2087-05-10T00:00:02Z"),
        )
        conn.commit()

        rows = fetch_all(
            conn,
            "select segment_id, model, dim, vector, created_at from segment_embeddings",
        )
    finally:
        conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["segment_id"] == "seg_emb"
    assert row["model"] == "campplus_voxceleb"
    assert row["dim"] == 3
    assert isinstance(row["vector"], bytes)
    assert row["vector"] == vector
    assert struct.unpack("<3f", row["vector"]) == (0.5, -1.25, 3.0)
    assert row["created_at"] == "2087-05-10T00:00:02Z"
