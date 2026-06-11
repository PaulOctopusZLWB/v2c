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
