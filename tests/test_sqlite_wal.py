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
