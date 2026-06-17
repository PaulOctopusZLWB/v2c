from __future__ import annotations

from personal_context_node.storage.sqlite import _run_migrations, connect, fetch_all, initialize


def test_person_voiceprints_table_exists(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(person_voiceprints)")

        # Re-running the DDL directly (as a fresh process would via _run_migrations)
        # must not raise; the per-process initialize() cache would otherwise hide a
        # non-idempotent create-table statement.
        _run_migrations(conn)
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert set(column_by_name) == {"person_id", "dim", "vector", "n_segments", "updated_at"}
    assert column_by_name["person_id"]["pk"] == 1
    assert column_by_name["person_id"]["type"].lower() == "text"
    assert column_by_name["dim"]["type"].lower() == "integer"
    assert column_by_name["dim"]["notnull"] == 1
    assert column_by_name["vector"]["type"].lower() == "blob"
    assert column_by_name["vector"]["notnull"] == 1
    assert column_by_name["n_segments"]["type"].lower() == "integer"
    assert column_by_name["n_segments"]["notnull"] == 1
    assert column_by_name["updated_at"]["type"].lower() == "text"
    assert column_by_name["updated_at"]["notnull"] == 1


def test_person_voiceprints_accepts_blob_row(tmp_path) -> None:
    import struct

    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        # FK is enforced (connect() runs `pragma foreign_keys = on`): person_id references
        # persons(person_id), so insert the parent person first.
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_vp", "Voiceprint Person", "contact", 0, "2087-05-10T00:00:00Z", "2087-05-10T00:00:00Z"),
        )

        vector = struct.pack("<3f", 0.5, -1.25, 3.0)
        conn.execute(
            "insert into person_voiceprints (person_id, dim, vector, n_segments, updated_at) values (?, ?, ?, ?, ?)",
            ("per_vp", 3, vector, 7, "2087-05-10T00:00:02Z"),
        )
        conn.commit()

        rows = fetch_all(
            conn,
            "select person_id, dim, vector, n_segments, updated_at from person_voiceprints",
        )
    finally:
        conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["person_id"] == "per_vp"
    assert row["dim"] == 3
    assert isinstance(row["vector"], bytes)
    assert row["vector"] == vector
    assert struct.unpack("<3f", row["vector"]) == (0.5, -1.25, 3.0)
    assert row["n_segments"] == 7
    assert row["updated_at"] == "2087-05-10T00:00:02Z"
