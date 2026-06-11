from __future__ import annotations

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
            conn.execute(
                """
                insert into archive_records (
                  archive_record_id, audio_file_id, source_path, archive_path,
                  sha256, verified, archived_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"arc_{uuid4().hex}",
                    row["audio_file_id"],
                    str(source_path),
                    str(result.archive_path),
                    row["sha256"],
                    1,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute("update audio_files set status = 'archived' where audio_file_id = ?", (row["audio_file_id"],))
            archived += 1
        conn.commit()
        return ArchiveCompletedAudioResult(files_archived=archived, files_pending=pending)
    finally:
        conn.close()


def _archive_relative_path(*, config: AppConfig, source_path: Path) -> Path:
    try:
        return source_path.relative_to(config.data_dir)
    except ValueError:
        return Path("audio") / "raw" / source_path.name
