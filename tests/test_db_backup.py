from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.db_backup import backup_sqlite_database
from personal_context_node.storage.sqlite import connect, initialize


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def test_backup_sqlite_database_creates_verified_copy_without_mutating_source(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_a', 'Alice', 'contact', 0, 'now', 'now')")
        conn.commit()
    finally:
        conn.close()

    before = _sha256(config.database_path)
    result = backup_sqlite_database(config=config, timestamp="20870510T120000")
    after = _sha256(config.database_path)

    assert after == before
    assert result.backup_path.exists()
    assert result.manifest_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == before
    assert manifest["backup_sha256"] == _sha256(result.backup_path)
    backup_conn = sqlite3.connect(result.backup_path)
    try:
        assert backup_conn.execute("select display_name from persons where person_id='per_a'").fetchone()[0] == "Alice"
    finally:
        backup_conn.close()
