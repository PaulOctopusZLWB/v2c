from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.clean_abnormal_2087_imports import clean_abnormal_imports


def test_clean_abnormal_imports_quarantines_bad_sources_and_deletes_derived_rows(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    raw_root = tmp_path / "data" / "audio" / "raw"
    db_path = tmp_path / "data" / "db" / "personal_context.sqlite"
    quarantine = tmp_path / "quarantine"
    inbox.mkdir()
    (raw_root / "2087-05-10").mkdir(parents=True)
    db_path.parent.mkdir(parents=True)

    bad_source = inbox / "TX01_MIC020_20870510_173557_orig.wav"
    good_source = inbox / "TX01_MIC020_20260609_173557_orig.wav"
    bad_raw = raw_root / "2087-05-10" / bad_source.name
    bad_source.write_bytes(b"bad-source")
    good_source.write_bytes(b"good-source")
    bad_raw.write_bytes(b"bad-raw")

    _seed_db(db_path, source_path=bad_source, raw_path=bad_raw)

    dry_run = clean_abnormal_imports(
        db_path=db_path,
        inbox_dir=inbox,
        raw_audio_dir=raw_root,
        quarantine_dir=quarantine,
        apply=False,
    )

    assert dry_run.bad_audio_files == 1
    assert dry_run.bad_segments == 1
    assert dry_run.planned_source_files == 1
    assert dry_run.planned_raw_files == 1
    assert bad_source.exists()

    applied = clean_abnormal_imports(
        db_path=db_path,
        inbox_dir=inbox,
        raw_audio_dir=raw_root,
        quarantine_dir=quarantine,
        apply=True,
        timestamp="20260626T120000",
    )

    assert applied.bad_audio_files == 1
    assert applied.moved_source_files == 1
    assert applied.moved_raw_files == 1
    assert not bad_source.exists()
    assert good_source.exists()
    assert (quarantine / "20260626T120000" / "inbox" / bad_source.name).exists()
    assert (quarantine / "20260626T120000" / "raw" / "2087-05-10" / bad_source.name).exists()
    assert (db_path.parent / "personal_context.sqlite.backup-20260626T120000").exists()

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("select count(*) from audio_files").fetchone()[0] == 0
        assert conn.execute("select count(*) from transcript_segments").fetchone()[0] == 0
        assert conn.execute("select count(*) from sessions").fetchone()[0] == 0
        assert conn.execute("select count(*) from tasks").fetchone()[0] == 0
    finally:
        conn.close()


def _seed_db(db_path: Path, *, source_path: Path, raw_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            create table audio_files (
              audio_file_id text primary key,
              source_path text not null,
              local_raw_path text not null,
              recorded_at text not null
            );
            create table transcript_segments (
              segment_id text primary key,
              audio_file_id text not null,
              session_id text,
              evidence_id text not null
            );
            create table sessions (
              session_id text primary key,
              date_key text not null
            );
            create table tasks (
              task_id text primary key,
              task_type text not null,
              target_type text not null,
              target_id text not null
            );
            create table audio_chunks (chunk_id text primary key, audio_file_id text not null);
            create table segment_embeddings (segment_id text primary key);
            create table segment_emotions (segment_id text primary key);
            create table transcript_segment_reviews (segment_id text primary key);
            create table segment_person_overrides (segment_id text primary key);
            create table evidence_refs (evidence_id text primary key, source_id text not null);
            create table session_viewpoint_state (session_id text primary key);
            create table archive_records (archive_record_id text primary key, audio_file_id text);
            create table daily_reports (date_key text primary key);
            create table daily_summaries (day text primary key);
            create table memory_candidates (candidate_id text primary key, date_key text);
            """
        )
        conn.execute(
            "insert into audio_files values (?, ?, ?, ?)",
            ("aud_bad", str(source_path), str(raw_path), "2087-05-10T17:35:57+08:00"),
        )
        conn.execute("insert into transcript_segments values (?, ?, ?, ?)", ("seg_bad", "aud_bad", "ses_bad", "ev_bad"))
        conn.execute("insert into sessions values (?, ?)", ("ses_bad", "2087-05-10"))
        conn.execute("insert into tasks values (?, ?, ?, ?)", ("task_audio", "transcribe_diarize", "audio_file", "aud_bad"))
        conn.execute("insert into tasks values (?, ?, ?, ?)", ("task_day", "session_derive", "date_key", "2087-05-10"))
        conn.commit()
    finally:
        conn.close()
