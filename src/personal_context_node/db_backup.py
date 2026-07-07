from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig


@dataclass(frozen=True)
class DbBackupResult:
    backup_path: Path
    manifest_path: Path
    source_sha256: str
    backup_sha256: str


def backup_sqlite_database(*, config: AppConfig, timestamp: str | None = None) -> DbBackupResult:
    """Create a verified SQLite backup without mutating the source database file."""
    source_path = config.database_path
    if not source_path.exists():
        raise FileNotFoundError(f"database does not exist: {source_path}")

    backups_dir = config.data_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backups_dir / f"personal_context-{stamp}.sqlite"
    manifest_path = backups_dir / f"personal_context-{stamp}.sha256.json"
    if backup_path.exists() or manifest_path.exists():
        raise FileExistsError(f"backup already exists for timestamp: {stamp}")

    source_sha256 = _sha256(source_path)
    source_uri = f"file:{source_path}?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    backup_conn = sqlite3.connect(backup_path)
    try:
        source_conn.backup(backup_conn)
    finally:
        backup_conn.close()
        source_conn.close()

    backup_sha256 = _sha256(backup_path)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source_path),
        "backup_path": str(backup_path),
        "source_sha256": source_sha256,
        "backup_sha256": backup_sha256,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return DbBackupResult(
        backup_path=backup_path,
        manifest_path=manifest_path,
        source_sha256=source_sha256,
        backup_sha256=backup_sha256,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
