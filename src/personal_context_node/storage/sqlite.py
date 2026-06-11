from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
create table if not exists schema_migrations (
  version integer primary key,
  name text not null,
  applied_at text not null default current_timestamp
);

create table if not exists audio_files (
  audio_file_id text primary key,
  source_device text not null,
  source_path text not null,
  source_size_bytes integer not null default 0,
  source_mtime_ns integer not null default 0,
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
  memory_card_id text,
  date_key text,
  normalized_claim_hash text
);

create table if not exists evidence_refs (
  evidence_id text primary key,
  source_type text not null,
  source_id text not null,
  quote text not null
);

create table if not exists memory_cards (
  card_id text primary key,
  current_version integer not null default 1,
  owner_did text not null,
  claim_type text not null,
  claim text not null,
  source_type text not null default 'confirmed_generated',
  confidence real,
  observed_at text,
  valid_from text,
  valid_until text,
  subject_json text not null,
  evidence_refs_json text not null,
  candidate_claim text,
  visibility_json text not null default '{"type":"private"}',
  tags_json text not null default '[]',
  status text not null,
  source_event_hash text not null,
  created_at text not null,
  updated_at text not null default ''
);

create view if not exists active_memory_cards as
select *
from memory_cards
where status = 'active';

create table if not exists memory_annotations (
  annotation_id text primary key,
  target_card_id text not null,
  author_did text not null,
  annotation_type text not null,
  body text not null,
  status text not null,
  source_event_hash text not null,
  created_at text not null
);

create view if not exists active_memory_annotations as
select memory_annotations.*
from memory_annotations
join active_memory_cards
  on active_memory_cards.card_id = memory_annotations.target_card_id
where memory_annotations.status = 'active';

create table if not exists identity_profiles (
  identity_id text primary key,
  display_name text not null,
  public_key_algorithm text not null,
  public_key_multibase text not null,
  predecessor_identity_id text,
  predecessor_rotation_event_hash text,
  source_event_hash text not null,
  created_at text not null,
  updated_at text not null
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

create table if not exists summaries (
  summary_id text primary key,
  summary_type text not null,
  target_type text not null,
  target_id text not null,
  prompt_version text,
  model_name text,
  content_json text not null,
  created_at text not null,
  updated_at text not null,
  unique(summary_type, target_type, target_id, prompt_version)
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
  exclude_from_memory integer not null default 0,
  created_at text not null,
  updated_at text not null
);

create table if not exists persons (
  person_id text primary key,
  display_name text not null,
  person_type text not null,
  is_self integer not null default 0,
  public_identity_id text,
  created_at text not null,
  updated_at text not null
);

create unique index if not exists idx_persons_self
on persons(is_self) where is_self = 1;

create table if not exists speaker_clusters (
  speaker_cluster_id text primary key,
  label text not null,
  source_type text not null,
  source_ref text,
  created_at text not null
);

create table if not exists speaker_mappings (
  speaker text primary key,
  person_label text not null,
  updated_at text not null,
  speaker_cluster_id text,
  person_id text
);

create table if not exists segment_person_overrides (
  segment_id text primary key,
  person_label text not null,
  updated_at text not null,
  person_id text
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

create table if not exists sync_logs (
  sync_log_id text primary key,
  source text not null,
  target_id text,
  status text not null,
  message text not null,
  created_at text not null
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
  event_hash text primary key,
  event_id text unique,
  event_type text not null,
  signer_did text not null,
  owner_id text,
  owner_sequence integer,
  prev_event_hash text,
  envelope_version text,
  object_id text,
  object_version integer,
  payload_type text,
  payload_encoding text,
  created_at text not null default '',
  payload_json text not null,
  raw_event_json text,
  signing_body_json text,
  canonical_signing_body_hash text,
  signature_algorithm text,
  public_key_id text,
  signature_value text,
  trust_status text not null default 'trusted',
  event_json text not null default '{}',
  signature text not null,
  public_key text not null,
  verified integer not null
);

create unique index if not exists idx_signed_events_trusted_owner_seq
on signed_events(owner_id, owner_sequence) where trust_status = 'trusted';

create unique index if not exists idx_signed_events_trusted_object_version
on signed_events(object_id, object_version) where trust_status = 'trusted';

create index if not exists idx_signed_events_owner_seq
on signed_events(owner_id, owner_sequence);

create view if not exists v_segment_attribution as
select
  ts.segment_id,
  coalesce(override.person_id, mapping.person_id) as person_id,
  case
    when override.person_id is not null then 'override'
    when mapping.person_id is not null then 'cluster_mapping'
  end as attribution_source
from transcript_segments ts
left join segment_person_overrides override on override.segment_id = ts.segment_id
left join speaker_mappings mapping on mapping.speaker = ts.speaker;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_column(conn, "audio_files", "source_size_bytes", "integer not null default 0")
    _ensure_column(conn, "audio_files", "source_mtime_ns", "integer not null default 0")
    _ensure_column(conn, "speaker_mappings", "speaker_cluster_id", "text")
    _ensure_column(conn, "speaker_mappings", "person_id", "text")
    _ensure_column(conn, "segment_person_overrides", "person_id", "text")
    _ensure_column(conn, "memory_candidates", "date_key", "text")
    _ensure_column(conn, "memory_candidates", "normalized_claim_hash", "text")
    _ensure_column(conn, "sessions", "exclude_from_memory", "integer not null default 0")
    _ensure_column(conn, "memory_cards", "source_type", "text not null default 'confirmed_generated'")
    _ensure_column(conn, "memory_cards", "current_version", "integer not null default 1")
    _ensure_column(conn, "memory_cards", "confidence", "real")
    _ensure_column(conn, "memory_cards", "observed_at", "text")
    _ensure_column(conn, "memory_cards", "valid_from", "text")
    _ensure_column(conn, "memory_cards", "valid_until", "text")
    _ensure_column(conn, "memory_cards", "visibility_json", "text not null default '{\"type\":\"private\"}'")
    _ensure_column(conn, "memory_cards", "tags_json", "text not null default '[]'")
    _ensure_column(conn, "memory_cards", "updated_at", "text not null default ''")
    conn.execute(
        "insert or ignore into schema_migrations (version, name) values (?, ?)",
        (1, "base_schema"),
    )
    conn.commit()


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")
