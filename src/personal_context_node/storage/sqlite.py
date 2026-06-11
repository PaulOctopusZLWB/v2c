from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
create table if not exists audio_files (
  audio_file_id text primary key,
  source_device text not null,
  source_path text not null,
  local_raw_path text not null,
  sha256 text not null,
  duration_ms integer not null,
  recorded_at text not null,
  imported_at text not null,
  status text not null,
  unique(source_path, sha256)
);

create table if not exists transcript_segments (
  segment_id text primary key,
  audio_file_id text not null references audio_files(audio_file_id),
  chunk_id text,
  session_id text,
  start_ms integer not null,
  end_ms integer not null,
  text text not null,
  language text not null,
  speaker text not null,
  evidence_id text not null unique,
  confidence real,
  asr_backend text not null default 'mock_first_milestone',
  model_name text not null default 'mock',
  model_version text not null default 'mock',
  asr_run_id text,
  is_active integer not null default 1,
  created_at text not null default ''
);

create table if not exists speech_ranges (
  speech_range_id text primary key,
  audio_file_id text not null references audio_files(audio_file_id),
  start_ms integer not null,
  end_ms integer not null,
  vad_backend text not null,
  unique(audio_file_id, start_ms, end_ms, vad_backend)
);

create table if not exists audio_chunks (
  chunk_id text primary key,
  audio_file_id text not null references audio_files(audio_file_id),
  speech_range_id text not null references speech_ranges(speech_range_id),
  source_start_ms integer not null,
  source_end_ms integer not null,
  local_chunk_path text not null,
  status text not null,
  unique(audio_file_id, source_start_ms, source_end_ms)
);

create table if not exists memory_candidates (
  candidate_id text primary key,
  candidate_claim text not null,
  claim_type text not null,
  subject_json text not null,
  confidence real not null,
  evidence_refs_json text not null,
  status text not null,
  memory_card_id text
);

create table if not exists daily_summaries (
  day text primary key,
  summary text not null,
  todos_json text not null,
  facts_json text not null,
  inferences_json text not null,
  prompt_version text not null,
  created_at text not null
);

create table if not exists daily_reports (
  day text primary key,
  status text not null,
  updated_at text not null,
  error text
);

create table if not exists sessions (
  session_id text primary key,
  date_key text not null,
  started_at text not null,
  ended_at text not null,
  source text not null,
  segment_count integer not null,
  active_speech_ms integer not null,
  first_segment_id text not null unique,
  created_at text not null,
  updated_at text not null
);

create table if not exists speaker_mappings (
  speaker text primary key,
  person_label text not null,
  updated_at text not null
);

create table if not exists segment_person_overrides (
  segment_id text primary key,
  person_label text not null,
  updated_at text not null
);

create table if not exists archive_records (
  archive_record_id text primary key,
  audio_file_id text not null references audio_files(audio_file_id),
  source_path text not null,
  archive_path text not null,
  sha256 text not null,
  verified integer not null,
  archived_at text not null
);

create table if not exists job_runs (
  run_id text primary key,
  job_name text not null,
  status text not null,
  started_at text not null,
  finished_at text,
  error text
);

create table if not exists tasks (
  task_id text primary key,
  task_type text not null,
  target_type text not null,
  target_id text not null,
  status text not null,
  attempt_count integer not null default 0,
  claimed_by_run_id text,
  claimed_at text,
  started_at text,
  finished_at text,
  last_error text,
  created_at text not null,
  unique(task_type, target_type, target_id)
);

create table if not exists signed_events (
  event_id text primary key,
  event_type text not null,
  signer_did text not null,
  created_at text not null default '',
  payload_json text not null,
  event_json text not null default '{}',
  signature text not null,
  public_key text not null,
  verified integer not null
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]
