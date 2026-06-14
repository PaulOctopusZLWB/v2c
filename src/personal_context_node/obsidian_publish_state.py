from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def note_was_user_edited(*, config: AppConfig, note_path: Path, edit_grace_seconds: int) -> bool:
    """True if the note changed since the system last wrote it AND is within edit grace.

    Implements §29.6 change detection (file mtime + content hash): a re-publish must skip
    a note the user is actively editing, but must NOT skip its own prior output (whose
    content still matches the recorded digest).
    """
    if edit_grace_seconds <= 0 or not note_path.exists():
        return False
    if time.time() - note_path.stat().st_mtime >= edit_grace_seconds:
        return False
    current = _sha256_text(note_path.read_text(encoding="utf-8"))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn, "select content_sha256 from note_digests where note_path = ?", (str(note_path),)
        )
    finally:
        conn.close()
    if not rows:
        return False  # never published by us — first publish, do not skip
    return current != str(rows[0]["content_sha256"])


def record_note_digest(*, config: AppConfig, note_path: Path, content: str) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into note_digests (note_path, content_sha256, updated_at)
            values (?, ?, ?)
            on conflict(note_path) do update set
              content_sha256 = excluded.content_sha256,
              updated_at = excluded.updated_at
            """,
            (str(note_path), _sha256_text(content), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
