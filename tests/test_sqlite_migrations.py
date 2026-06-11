from __future__ import annotations

from personal_context_node.storage.sqlite import connect, fetch_all, initialize


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
