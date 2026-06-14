from __future__ import annotations

from pathlib import Path

from personal_context_node.storage.sqlite import connect, initialize


def test_connect_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "test.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        initialize(conn)
        journal_mode = conn.execute("pragma journal_mode").fetchone()[0]
        busy_timeout = conn.execute("pragma busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert journal_mode.lower() == "wal"
    assert busy_timeout >= 5000


def test_initialize_skips_ddl_when_already_migrated_so_reads_dont_block(tmp_path: Path) -> None:
    # Regression: initialize() used to run schema DDL (a write) on every connection,
    # so a read request would contend with the background worker's write lock and
    # raise `database is locked`. It must now be a no-op once the file is migrated.
    db = tmp_path / "db" / "concur.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    writer = connect(db)
    initialize(writer)
    # Hold an open write transaction — any DDL attempt by another connection blocks.
    writer.execute("begin immediate")
    writer.execute(
        "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at)"
        " values ('p1', 'P', 'self', 1, 't', 't')"
    )
    try:
        reader = connect(db)
        reader.execute("pragma busy_timeout = 300")  # fail fast if the guard regresses
        initialize(reader)  # must NOT issue DDL now → no 'database is locked'
        from personal_context_node.storage.sqlite import fetch_all

        rows = fetch_all(reader, "select count(*) as n from persons")
        assert rows[0]["n"] == 0  # WAL read sees committed state, uncommitted writer row hidden
        reader.close()
    finally:
        writer.rollback()
        writer.close()
