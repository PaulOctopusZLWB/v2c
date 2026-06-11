from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.archive import ArchivePort
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class ArchiveCompletedAudioResult:
    files_archived: int
    files_pending: int
    events_archived: int = 0
    events_pending: int = 0


def archive_completed_audio(*, config: AppConfig, archive: ArchivePort) -> ArchiveCompletedAudioResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select audio_file_id, local_raw_path, sha256
            from audio_files
            where status != 'archived'
            order by imported_at
            """,
        )
        archived = 0
        pending = 0
        for row in rows:
            source_path = Path(row["local_raw_path"])
            relative_path = _archive_relative_path(config=config, source_path=source_path)
            result = archive.archive_file(
                source_path=source_path,
                relative_path=relative_path,
                expected_sha256=row["sha256"],
            )
            if not result.verified:
                pending += 1
                continue
            archived_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                insert into archive_records (
                  archive_record_id, target_type, target_id, audio_file_id,
                  source_path, archive_path, sha256, status, verified, archived_at,
                  created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"arc_{uuid4().hex}",
                    "audio_file",
                    row["audio_file_id"],
                    row["audio_file_id"],
                    str(source_path),
                    str(result.archive_path),
                    row["sha256"],
                    "verified",
                    1,
                    archived_at,
                    archived_at,
                    archived_at,
                ),
            )
            conn.execute("update audio_files set status = 'archived' where audio_file_id = ?", (row["audio_file_id"],))
            archived += 1
        events_archived, events_pending = _archive_signed_events(conn, config=config, archive=archive)
        conn.commit()
        return ArchiveCompletedAudioResult(
            files_archived=archived,
            files_pending=pending,
            events_archived=events_archived,
            events_pending=events_pending,
        )
    finally:
        conn.close()


def _archive_relative_path(*, config: AppConfig, source_path: Path) -> Path:
    try:
        return source_path.relative_to(config.data_dir)
    except ValueError:
        return Path("audio") / "raw" / source_path.name


def _archive_signed_events(conn, *, config: AppConfig, archive: ArchivePort) -> tuple[int, int]:
    rows = fetch_all(
        conn,
        """
        select raw_event_json
        from signed_events
        where trust_status in ('trusted', 'unsupported')
        order by created_at, event_hash
        """,
    )
    if not rows:
        return 0, 0
    source_path = config.data_dir / "exports" / "signed_events.jsonl"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("\n".join(str(row["raw_event_json"]) for row in rows) + "\n", encoding="utf-8")
    expected_sha256 = _sha256(source_path)
    result = archive.archive_file(
        source_path=source_path,
        relative_path=Path("events") / "signed_events.jsonl",
        expected_sha256=expected_sha256,
    )
    if not result.verified:
        return 0, 1
    archived_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        insert into archive_records (
          archive_record_id, target_type, target_id, audio_file_id,
          source_path, archive_path, sha256, status, verified, archived_at,
          created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(target_type, target_id, archive_path) do update set
          sha256 = excluded.sha256,
          status = excluded.status,
          verified = excluded.verified,
          archived_at = excluded.archived_at,
          updated_at = excluded.updated_at
        """,
        (
            f"arc_{uuid4().hex}",
            "signed_events",
            "all",
            None,
            str(source_path),
            str(result.archive_path),
            expected_sha256,
            "verified",
            1,
            archived_at,
            archived_at,
            archived_at,
        ),
    )
    return 1, 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
