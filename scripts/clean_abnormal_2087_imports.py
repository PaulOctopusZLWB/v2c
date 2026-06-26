from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BAD_RECORDED_AT_CLAUSE = "(recorded_at >= '2080' or recorded_at < '2020')"
FILENAME_PATTERN = re.compile(r"^(?P<prefix>TX\d+_MIC\d+)_(?P<date>\d{8})_(?P<time>\d{6})_orig\.[wW][aA][vV]$")


@dataclass(frozen=True)
class CleanAbnormalImportsResult:
    bad_audio_files: int
    bad_segments: int
    bad_sessions: int
    planned_source_files: int
    planned_raw_files: int
    moved_source_files: int
    moved_raw_files: int
    backup_path: Path | None
    quarantine_run_dir: Path


@dataclass(frozen=True)
class BadAudioSource:
    audio_file_id: str
    source_path: Path
    local_raw_path: Path


def clean_abnormal_imports(
    *,
    db_path: Path,
    inbox_dir: Path,
    raw_audio_dir: Path,
    quarantine_dir: Path,
    apply: bool = False,
    corrected_date: str = "20260609",
    timestamp: str | None = None,
) -> CleanAbnormalImportsResult:
    run_timestamp = timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    quarantine_run_dir = quarantine_dir / run_timestamp

    bad_sources = _bad_sources(db_path)
    _assert_sources_are_paired(bad_sources, inbox_dir=inbox_dir, corrected_date=corrected_date)

    planned_source_files = sum(1 for source in bad_sources if source.source_path.exists())
    planned_raw_files = sum(1 for source in bad_sources if source.local_raw_path.exists())
    bad_segments, bad_sessions = _bad_derived_counts(db_path)

    backup_path: Path | None = None
    moved_source_files = 0
    moved_raw_files = 0

    if apply:
        quarantine_run_dir.mkdir(parents=True, exist_ok=False)
        backup_path = _backup_database(db_path, run_timestamp)
        _delete_bad_database_rows(db_path)
        moved_raw_files = _move_files(
            [source.local_raw_path for source in bad_sources],
            root=raw_audio_dir,
            destination_root=quarantine_run_dir / "raw",
        )
        moved_source_files = _move_files(
            [source.source_path for source in bad_sources],
            root=inbox_dir,
            destination_root=quarantine_run_dir / "inbox",
        )

    return CleanAbnormalImportsResult(
        bad_audio_files=len(bad_sources),
        bad_segments=bad_segments,
        bad_sessions=bad_sessions,
        planned_source_files=planned_source_files,
        planned_raw_files=planned_raw_files,
        moved_source_files=moved_source_files,
        moved_raw_files=moved_raw_files,
        backup_path=backup_path,
        quarantine_run_dir=quarantine_run_dir,
    )


def _bad_sources(db_path: Path) -> list[BadAudioSource]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            select audio_file_id, source_path, local_raw_path
            from audio_files
            where {BAD_RECORDED_AT_CLAUSE}
            order by source_path
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        BadAudioSource(
            audio_file_id=str(row["audio_file_id"]),
            source_path=Path(str(row["source_path"])),
            local_raw_path=Path(str(row["local_raw_path"])),
        )
        for row in rows
    ]


def _assert_sources_are_paired(bad_sources: list[BadAudioSource], *, inbox_dir: Path, corrected_date: str) -> None:
    missing_pairs: list[str] = []
    for source in bad_sources:
        if source.source_path.parent != inbox_dir:
            raise RuntimeError(f"unexpected bad source outside inbox: {source.source_path}")
        match = FILENAME_PATTERN.match(source.source_path.name)
        if not match or not match.group("date").startswith("2087"):
            raise RuntimeError(f"unexpected bad source filename: {source.source_path.name}")
        corrected_name = f"{match.group('prefix')}_{corrected_date}_{match.group('time')}_orig{source.source_path.suffix}"
        if not (inbox_dir / corrected_name).exists():
            missing_pairs.append(f"{source.source_path.name} -> {corrected_name}")
    if missing_pairs:
        raise RuntimeError("missing corrected source pairs:\n" + "\n".join(missing_pairs))


def _bad_derived_counts(db_path: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        segments = conn.execute(
            f"""
            select count(*)
            from transcript_segments
            where audio_file_id in (select audio_file_id from audio_files where {BAD_RECORDED_AT_CLAUSE})
            """
        ).fetchone()[0]
        sessions = conn.execute("select count(*) from sessions where date_key like '2087-%'").fetchone()[0]
    finally:
        conn.close()
    return int(segments), int(sessions)


def _backup_database(db_path: Path, timestamp: str) -> Path:
    backup_path = db_path.with_name(f"{db_path.name}.backup-{timestamp}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _delete_bad_database_rows(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            f"""
            begin;
            create temp table bad_audio as
              select audio_file_id from audio_files where {BAD_RECORDED_AT_CLAUSE};
            create temp table bad_segments as
              select segment_id from transcript_segments where audio_file_id in (select audio_file_id from bad_audio);
            create temp table bad_sessions as
              select session_id from sessions where date_key like '2087-%'
              union
              select distinct session_id from transcript_segments
              where session_id is not null and segment_id in (select segment_id from bad_segments);
            create temp table bad_dates as
              select distinct substr(recorded_at, 1, 10) as date_key
              from audio_files where {BAD_RECORDED_AT_CLAUSE}
              union
              select distinct date_key from sessions where date_key like '2087-%';

            delete from segment_embeddings where segment_id in (select segment_id from bad_segments);
            delete from segment_emotions where segment_id in (select segment_id from bad_segments);
            delete from transcript_segment_reviews where segment_id in (select segment_id from bad_segments);
            delete from segment_person_overrides where segment_id in (select segment_id from bad_segments);
            delete from evidence_refs where source_id in (select segment_id from bad_segments);
            delete from transcript_segments where segment_id in (select segment_id from bad_segments);
            delete from audio_chunks where audio_file_id in (select audio_file_id from bad_audio);
            delete from session_viewpoint_state where session_id in (select session_id from bad_sessions);
            delete from sessions where session_id in (select session_id from bad_sessions);
            delete from archive_records where audio_file_id in (select audio_file_id from bad_audio);
            delete from tasks where target_id in (select audio_file_id from bad_audio);
            delete from tasks where target_type = 'date_key' and target_id in (select date_key from bad_dates);
            delete from daily_reports where date_key in (select date_key from bad_dates);
            delete from daily_summaries where day in (select date_key from bad_dates);
            delete from memory_candidates where date_key in (select date_key from bad_dates);
            delete from audio_files where audio_file_id in (select audio_file_id from bad_audio);
            commit;
            """
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _move_files(paths: list[Path], *, root: Path, destination_root: Path) -> int:
    moved = 0
    for path in paths:
        if not path.exists():
            continue
        relative = path.relative_to(root)
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
        moved += 1
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean duplicated abnormal 2087 PCN audio imports.")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--inbox-dir", type=Path, required=True)
    parser.add_argument("--raw-audio-dir", type=Path, required=True)
    parser.add_argument("--quarantine-dir", type=Path, required=True)
    parser.add_argument("--corrected-date", default="20260609")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = clean_abnormal_imports(
        db_path=args.db_path,
        inbox_dir=args.inbox_dir,
        raw_audio_dir=args.raw_audio_dir,
        quarantine_dir=args.quarantine_dir,
        corrected_date=args.corrected_date,
        apply=args.apply,
    )
    print(f"bad_audio_files={result.bad_audio_files}")
    print(f"bad_segments={result.bad_segments}")
    print(f"bad_sessions={result.bad_sessions}")
    print(f"planned_source_files={result.planned_source_files}")
    print(f"planned_raw_files={result.planned_raw_files}")
    print(f"moved_source_files={result.moved_source_files}")
    print(f"moved_raw_files={result.moved_raw_files}")
    print(f"backup_path={result.backup_path or ''}")
    print(f"quarantine_run_dir={result.quarantine_run_dir}")


if __name__ == "__main__":
    main()
