from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.archive import ArchivePort
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class ArchiveCompletedAudioResult:
    files_archived: int
    files_pending: int
    events_archived: int = 0
    events_pending: int = 0
    transcripts_archived: int = 0
    transcripts_pending: int = 0
    summaries_archived: int = 0
    summaries_pending: int = 0
    memory_candidates_archived: int = 0
    memory_candidates_pending: int = 0


@dataclass(frozen=True)
class CleanupArchivedAudioResult:
    files_removed: int
    files_pending: int


@dataclass(frozen=True)
class MarkCleanupEligibleResult:
    files_marked: int
    files_pending: int


def archive_status_rows(*, config: AppConfig, limit: int = 20) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select archive_record_id, target_type, target_id, source_path, archive_path,
                   sha256, status, verified, archived_at, last_error
            from archive_records
            order by archived_at desc, archive_record_id
            limit ?
            """,
            (limit,),
        )
    finally:
        conn.close()


def mark_cleanup_eligible_audio(*, config: AppConfig, archive: ArchivePort, archived_before: datetime) -> MarkCleanupEligibleResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select af.audio_file_id, ar.archive_path, ar.sha256
            from audio_files af
            join archive_records ar
              on ar.target_type = 'audio_file'
             and ar.target_id = af.audio_file_id
             and ar.verified = 1
             and ar.status = 'verified'
            where af.status = 'archived'
              and ar.archived_at < ?
            order by ar.archived_at, af.audio_file_id
            """,
            (archived_before.isoformat(),),
        )
        marked = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            archive_result = archive.verify_file(
                archive_path=Path(row["archive_path"]),
                expected_sha256=str(row["sha256"]),
            )
            if not archive_result.verified:
                continue
            conn.execute(
                "update audio_files set status = 'cleanup_eligible' where audio_file_id = ?",
                (row["audio_file_id"],),
            )
            conn.execute(
                """
                update archive_records
                set status = 'cleanup_eligible',
                    updated_at = ?
                where target_type = 'audio_file'
                  and target_id = ?
                  and archive_path = ?
                """,
                (now, row["audio_file_id"], row["archive_path"]),
            )
            marked += 1
        pending_rows = fetch_all(conn, "select count(*) as count from audio_files where status = 'archived'")
        conn.commit()
        return MarkCleanupEligibleResult(files_marked=marked, files_pending=int(pending_rows[0]["count"]))
    finally:
        conn.close()


def archive_completed_audio(*, config: AppConfig, archive: ArchivePort) -> ArchiveCompletedAudioResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select audio_file_id, local_raw_path, sha256
            from audio_files
            where status = 'imported'
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
                _upsert_archive_record(
                    conn,
                    target_type="audio_file",
                    target_id=str(row["audio_file_id"]),
                    audio_file_id=str(row["audio_file_id"]),
                    source_path=source_path,
                    archive_path=result.archive_path,
                    sha256=str(row["sha256"]),
                    status="pending",
                    verified=0,
                    last_error=result.reason or "archive verification failed",
                )
                pending += 1
                continue
            _upsert_archive_record(
                conn,
                target_type="audio_file",
                target_id=str(row["audio_file_id"]),
                audio_file_id=str(row["audio_file_id"]),
                source_path=source_path,
                archive_path=result.archive_path,
                sha256=str(row["sha256"]),
                status="verified",
                verified=1,
                last_error=None,
            )
            conn.execute("update audio_files set status = 'archived' where audio_file_id = ?", (row["audio_file_id"],))
            archived += 1
        events_archived, events_pending = _archive_signed_events(conn, config=config, archive=archive)
        transcripts_archived, transcripts_pending = _archive_rows_as_jsonl(
            conn,
            config=config,
            archive=archive,
            target_type="transcript_segments",
            target_id="all",
            source_filename="transcript_segments.jsonl",
            relative_path=Path("derived") / "transcript_segments.jsonl",
            sql="""
            select segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms,
                   absolute_start_at, absolute_end_at, text, language, speaker,
                   speaker_cluster_id, evidence_id, confidence, asr_backend,
                   model_name, model_version, decode_config_json, asr_run_id,
                   is_active, created_at
            from transcript_segments
            order by audio_file_id, start_ms, segment_id
            """,
        )
        summaries_archived, summaries_pending = _archive_rows_as_jsonl(
            conn,
            config=config,
            archive=archive,
            target_type="summaries",
            target_id="all",
            source_filename="summaries.jsonl",
            relative_path=Path("derived") / "summaries.jsonl",
            sql="""
            select summary_id, summary_type, target_type, target_id, prompt_version,
                   model_name, content_json, created_at, updated_at
            from summaries
            order by summary_type, target_type, target_id, summary_id
            """,
        )
        memory_candidates_archived, memory_candidates_pending = _archive_rows_as_jsonl(
            conn,
            config=config,
            archive=archive,
            target_type="memory_candidates",
            target_id="all",
            source_filename="memory_candidates.jsonl",
            relative_path=Path("derived") / "memory_candidates.jsonl",
            sql="""
            select candidate_id, source_type, candidate_claim, edited_claim, claim_type,
                   subject_json, confidence, evidence_refs_json, status, memory_card_id,
                   review_note_path, reviewed_at, created_card_id, date_key,
                   normalized_claim_hash, prompt_version, created_at, updated_at
            from memory_candidates
            order by date_key, candidate_id
            """,
        )
        conn.commit()
        return ArchiveCompletedAudioResult(
            files_archived=archived,
            files_pending=pending,
            events_archived=events_archived,
            events_pending=events_pending,
            transcripts_archived=transcripts_archived,
            transcripts_pending=transcripts_pending,
            summaries_archived=summaries_archived,
            summaries_pending=summaries_pending,
            memory_candidates_archived=memory_candidates_archived,
            memory_candidates_pending=memory_candidates_pending,
        )
    finally:
        conn.close()


def cleanup_archived_audio(*, config: AppConfig, archive: ArchivePort, archived_before: datetime) -> CleanupArchivedAudioResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select af.audio_file_id, af.local_raw_path, ar.archive_path, ar.sha256
            from audio_files af
            join archive_records ar
              on ar.target_type = 'audio_file'
             and ar.target_id = af.audio_file_id
             and ar.verified = 1
             and ar.status in ('verified', 'cleanup_eligible')
            where af.status = 'cleanup_eligible'
              and ar.archived_at < ?
            order by ar.archived_at, af.audio_file_id
            """,
            (archived_before.isoformat(),),
        )
        removed = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            archive_result = archive.verify_file(
                archive_path=Path(row["archive_path"]),
                expected_sha256=str(row["sha256"]),
            )
            if not archive_result.verified:
                continue
            local_path = Path(row["local_raw_path"])
            if local_path.exists():
                local_path.unlink()
            conn.execute(
                "update audio_files set status = 'locally_removed' where audio_file_id = ?",
                (row["audio_file_id"],),
            )
            conn.execute(
                """
                update archive_records
                set status = 'locally_removed',
                    updated_at = ?
                where target_type = 'audio_file'
                  and target_id = ?
                  and archive_path = ?
                """,
                (now, row["audio_file_id"], row["archive_path"]),
            )
            removed += 1
        pending_rows = fetch_all(conn, "select count(*) as count from audio_files where status = 'cleanup_eligible'")
        conn.commit()
        return CleanupArchivedAudioResult(files_removed=removed, files_pending=int(pending_rows[0]["count"]))
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
    write_text_atomic(source_path, "\n".join(str(row["raw_event_json"]) for row in rows) + "\n")
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


def _archive_rows_as_jsonl(
    conn,
    *,
    config: AppConfig,
    archive: ArchivePort,
    target_type: str,
    target_id: str,
    source_filename: str,
    relative_path: Path,
    sql: str,
) -> tuple[int, int]:
    rows = fetch_all(conn, sql)
    if not rows:
        return 0, 0
    source_path = config.data_dir / "exports" / source_filename
    source_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        source_path,
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
    )
    expected_sha256 = _sha256(source_path)
    result = archive.archive_file(
        source_path=source_path,
        relative_path=relative_path,
        expected_sha256=expected_sha256,
    )
    if not result.verified:
        return 0, 1
    _upsert_archive_record(
        conn,
        target_type=target_type,
        target_id=target_id,
        source_path=source_path,
        archive_path=result.archive_path,
        sha256=expected_sha256,
    )
    return 1, 0


def _upsert_archive_record(
    conn,
    *,
    target_type: str,
    target_id: str,
    audio_file_id: str | None = None,
    source_path: Path,
    archive_path: Path,
    sha256: str,
    status: str = "verified",
    verified: int = 1,
    last_error: str | None = None,
) -> None:
    archived_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        insert into archive_records (
          archive_record_id, target_type, target_id, audio_file_id,
          source_path, archive_path, sha256, status, verified, archived_at,
          last_error, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(target_type, target_id, archive_path) do update set
          sha256 = excluded.sha256,
          status = excluded.status,
          verified = excluded.verified,
          last_error = excluded.last_error,
          archived_at = excluded.archived_at,
          updated_at = excluded.updated_at
        """,
        (
            f"arc_{uuid4().hex}",
            target_type,
            target_id,
            audio_file_id,
            str(source_path),
            str(archive_path),
            sha256,
            status,
            verified,
            archived_at,
            last_error,
            archived_at,
            archived_at,
        ),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
