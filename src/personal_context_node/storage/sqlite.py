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
  unique(source_path, source_size_bytes, source_mtime_ns, sha256)
);

create table if not exists transcript_segments (
  segment_id text primary key,
  audio_file_id text not null references audio_files(audio_file_id),
  chunk_id text not null,
  session_id text,
  start_ms integer not null,
  end_ms integer not null,
  absolute_start_at text,
  absolute_end_at text,
  text text not null,
  language text not null,
  speaker text not null,
  speaker_cluster_id text,
  evidence_id text not null unique,
  confidence real,
  asr_backend text not null default 'mock_first_milestone',
  model_name text not null default 'mock',
  model_version text not null default 'mock',
  decode_config_json text,
  asr_tags_json text not null default '[]',
  asr_run_id text,
  is_active integer not null default 1,
  created_at text not null default ''
);

create index if not exists idx_segments_session_time
on transcript_segments(session_id, absolute_start_at);

create index if not exists idx_segments_audio_time
on transcript_segments(audio_file_id, start_ms, end_ms);

create table if not exists transcript_segment_reviews (
  segment_id text primary key references transcript_segments(segment_id),
  status text not null,
  reviewer text not null default 'local_user',
  note text,
  reviewed_at text not null,
  updated_at text not null
);

create index if not exists idx_segment_reviews_status
on transcript_segment_reviews(status, reviewed_at);

create table if not exists audio_chunks (
  chunk_id text primary key,
  audio_file_id text not null references audio_files(audio_file_id),
  local_work_path text not null default '',
  start_ms integer not null default 0,
  end_ms integer not null default 0,
  absolute_start_at text,
  absolute_end_at text,
  vad_backend text,
  vad_config_json text,
  created_at text not null default '',
  source_start_ms integer not null,
  source_end_ms integer not null,
  local_chunk_path text not null,
  status text not null,
  unique(audio_file_id, source_start_ms, source_end_ms)
);

create index if not exists idx_chunks_audio_time
on audio_chunks(audio_file_id, start_ms, end_ms);

create table if not exists memory_candidates (
  candidate_id text primary key,
  source_type text not null default 'llm_daily_context',
  candidate_claim text not null,
  edited_claim text,
  claim_type text not null,
  subject_json text not null,
  confidence real not null,
  evidence_refs_json text not null,
  status text not null,
  memory_card_id text,
  review_note_path text,
  reviewed_at text,
  created_card_id text,
  date_key text,
  normalized_claim_hash text,
  prompt_version text not null default 'unknown',
  created_at text not null default '',
  updated_at text not null default ''
);

create index if not exists idx_candidates_status
on memory_candidates(status);

create table if not exists evidence_refs (
  evidence_id text primary key,
  source_type text not null,
  source_ref text not null default '',
  source_id text not null,
  owner_id text,
  quote text not null,
  summary text,
  created_at text not null default ''
);

create table if not exists memory_cards (
  card_id text primary key,
  current_version integer not null default 1,
  owner_id text not null default '',
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

create table if not exists session_viewpoint_state (
  session_id text primary key,
  edited_content_json text,
  prompt_override text,
  status text not null default 'draft',
  source_fingerprint text,
  note_path text,
  published_at text,
  updated_at text not null
);

create table if not exists app_prompts (
  kind text primary key,
  template text not null,
  updated_at text not null
);

create table if not exists daily_reports (
  date_key text primary key,
  status text not null,
  note_path text,
  total_recorded_ms integer not null default 0,
  active_speech_ms integer not null default 0,
  self_speech_ms integer not null default 0,
  others_speech_ms integer not null default 0,
  generated_at text,
  reviewed_at text,
  error text,
  created_at text not null default '',
  updated_at text not null
);

create table if not exists sessions (
  session_id text primary key,
  date_key text not null,
  started_at text not null,
  ended_at text not null,
  source text not null,
  segment_count integer not null,
  active_speech_ms integer not null,
  primary_person_id text,
  name text,
  first_segment_id text not null unique,
  exclude_from_memory integer not null default 0,
  created_at text not null,
  updated_at text not null
);

create index if not exists idx_sessions_date
on sessions(date_key, started_at);

create table if not exists agent_sessions (
  agent_session_id text primary key,
  source_type text not null,
  source_path text not null,
  source_sha256 text not null,
  originator text,
  cli_version text,
  cwd text,
  model text,
  started_at text not null,
  ended_at text,
  title text,
  message_count integer not null default 0,
  tool_event_count integer not null default 0,
  created_at text not null,
  updated_at text not null
);

create index if not exists idx_agent_sessions_started
on agent_sessions(started_at);

create table if not exists agent_turns (
  agent_turn_id text primary key,
  agent_session_id text not null references agent_sessions(agent_session_id) on delete cascade,
  turn_index integer not null,
  role text not null,
  occurred_at text not null,
  text text not null,
  metadata_json text not null default '{}',
  created_at text not null
);

create unique index if not exists idx_agent_turns_session_index
on agent_turns(agent_session_id, turn_index);

create table if not exists agent_tool_events (
  agent_tool_event_id text primary key,
  agent_session_id text not null references agent_sessions(agent_session_id) on delete cascade,
  event_index integer not null,
  occurred_at text not null,
  tool_name text not null,
  call_id text,
  arguments_json text not null default '{}',
  output_text text,
  status text not null,
  created_at text not null
);

create unique index if not exists idx_agent_tool_events_session_index
on agent_tool_events(agent_session_id, event_index);

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
  speaker_mapping_id text,
  person_label text not null,
  speaker_cluster_id text,
  person_id text,
  confidence real,
  source text not null default 'speaker_review',
  created_at text not null default '',
  updated_at text not null
);

create table if not exists segment_person_overrides (
  segment_id text primary key,
  person_label text not null,
  updated_at text not null,
  person_id text,
  source text not null default 'manual'
);

create table if not exists session_participants (
  session_id text not null references sessions(session_id),
  person_id text not null references persons(person_id),
  status text not null check(status in ('present', 'absent', 'uncertain')),
  source text not null default 'manual',
  note text,
  updated_at text not null,
  primary key(session_id, person_id)
);

create index if not exists idx_session_participants_session
on session_participants(session_id);

create index if not exists idx_session_participants_person
on session_participants(person_id);

create table if not exists segment_identity_negative_feedback (
  segment_id text not null references transcript_segments(segment_id),
  person_id text not null references persons(person_id),
  session_id text,
  source text not null default 'manual',
  note text,
  updated_at text not null,
  primary key(segment_id, person_id)
);

create index if not exists idx_segment_identity_negative_session
on segment_identity_negative_feedback(session_id);

create index if not exists idx_segment_identity_negative_person
on segment_identity_negative_feedback(person_id);

create table if not exists segment_embeddings (
  segment_id text primary key references transcript_segments(segment_id),
  model text not null,
  dim integer not null,
  vector blob not null,
  created_at text not null
);

create index if not exists idx_segment_embeddings_model on segment_embeddings(model);

create table if not exists segment_emotions (
  segment_id text primary key references transcript_segments(segment_id),
  model text not null,
  label text not null,
  scores_json text not null,
  created_at text not null
);

create index if not exists idx_segment_emotions_label on segment_emotions(label);

create table if not exists person_voiceprints (
  person_id text primary key references persons(person_id),
  dim integer not null,
  vector blob not null,
  n_segments integer not null,
  updated_at text not null
);

create table if not exists archive_records (
  archive_record_id text primary key,
  target_type text not null default '',
  target_id text not null default '',
  audio_file_id text references audio_files(audio_file_id),
  source_path text not null,
  archive_path text not null,
  sha256 text not null,
  status text not null default 'verified',
  verified integer not null,
  archived_at text not null,
  last_error text,
  created_at text not null default '',
  updated_at text not null default ''
);

create table if not exists job_runs (
  run_id text primary key,
  job_name text not null,
  status text not null,
  started_at text not null,
  finished_at text,
  error text
);

create table if not exists note_digests (
  note_path text primary key,
  content_sha256 text not null,
  updated_at text not null default ''
);

create table if not exists settings (
  key text primary key,
  value text not null,
  updated_at text not null default ''
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
  priority integer not null default 100,
  retry_count integer not null default 0,
  max_retries integer not null default 3,
  attempt_count integer not null default 0,
  claimed_by_run_id text,
  claimed_at text,
  lease_expires_at text,
  started_at text,
  finished_at text,
  last_error text,
  available_at text not null default '',
  created_at text not null,
  updated_at text not null default '',
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
  trust_status text not null default 'unverified',
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

create index if not exists idx_signed_events_object
on signed_events(object_id, object_version);

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
left join speaker_mappings mapping on mapping.speaker_cluster_id = coalesce(ts.speaker_cluster_id, ts.speaker);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma busy_timeout = 30000")
    return conn


_INITIALIZED_DBS: set[str] = set()


def _main_db_file(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("pragma database_list").fetchone()
    except sqlite3.Error:
        return ""
    return str(row[2]) if row and row[2] else ""


def initialize(conn: sqlite3.Connection) -> None:
    """Apply schema + migrations once per DB file per process.

    The DDL below takes a write lock. Re-running it on every connection turned
    every read request (and the 1s SSE status poll) into a write-lock contender,
    which surfaced as `database is locked` 500s while the background worker was
    committing transcripts. Connection-level pragmas (WAL, busy_timeout) live in
    connect(), so skipping the DDL on subsequent connections to an already-migrated
    file is safe.
    """
    dbfile = _main_db_file(conn)
    if dbfile and dbfile in _INITIALIZED_DBS:
        return
    _run_migrations(conn)
    if dbfile:
        _INITIALIZED_DBS.add(dbfile)


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_column(conn, "audio_files", "source_size_bytes", "integer not null default 0")
    _ensure_column(conn, "audio_files", "source_mtime_ns", "integer not null default 0")
    _relax_audio_files_source_identity(conn)
    conn.execute("drop index if exists idx_audio_files_source_identity")
    conn.execute(
        """
        create unique index if not exists idx_audio_files_source_identity
        on audio_files(source_device, source_path, source_size_bytes, source_mtime_ns, sha256)
        """
    )
    # local_raw_path is the unique raw-store path (§27.1): defense-in-depth against
    # two records pointing at the same local evidence file.
    conn.execute(
        "create unique index if not exists idx_audio_files_local_raw_path on audio_files(local_raw_path)"
    )
    conn.execute("create index if not exists idx_audio_files_recorded_at on audio_files(recorded_at)")
    conn.execute("create index if not exists idx_audio_files_status on audio_files(status)")
    _ensure_column(conn, "transcript_segments", "absolute_start_at", "text")
    _ensure_column(conn, "transcript_segments", "absolute_end_at", "text")
    _ensure_column(conn, "transcript_segments", "speaker_cluster_id", "text")
    _ensure_column(conn, "transcript_segments", "decode_config_json", "text")
    _ensure_column(conn, "transcript_segments", "asr_tags_json", "text not null default '[]'")
    conn.execute(
        """
        update transcript_segments
        set speaker_cluster_id = speaker
        where (speaker_cluster_id is null or speaker_cluster_id = '') and speaker is not null
        """
    )
    conn.execute("create index if not exists idx_segments_session_time on transcript_segments(session_id, absolute_start_at)")
    conn.execute("create index if not exists idx_segments_audio_time on transcript_segments(audio_file_id, start_ms, end_ms)")
    conn.execute("create index if not exists idx_segments_cluster on transcript_segments(speaker_cluster_id)")
    conn.execute(
        """
        create table if not exists transcript_segment_reviews (
          segment_id text primary key references transcript_segments(segment_id),
          status text not null,
          reviewer text not null default 'local_user',
          note text,
          reviewed_at text not null,
          updated_at text not null
        )
        """
    )
    conn.execute("create index if not exists idx_segment_reviews_status on transcript_segment_reviews(status, reviewed_at)")
    _ensure_column(conn, "audio_chunks", "local_work_path", "text not null default ''")
    _ensure_column(conn, "audio_chunks", "start_ms", "integer not null default 0")
    _ensure_column(conn, "audio_chunks", "end_ms", "integer not null default 0")
    _ensure_column(conn, "audio_chunks", "absolute_start_at", "text")
    _ensure_column(conn, "audio_chunks", "absolute_end_at", "text")
    _ensure_column(conn, "audio_chunks", "vad_backend", "text")
    _ensure_column(conn, "audio_chunks", "vad_config_json", "text")
    _ensure_column(conn, "audio_chunks", "created_at", "text not null default ''")
    conn.execute("update audio_chunks set local_work_path = local_chunk_path where local_work_path = ''")
    conn.execute("update audio_chunks set start_ms = source_start_ms where start_ms = 0")
    conn.execute("update audio_chunks set end_ms = source_end_ms where end_ms = 0")
    _remove_speech_ranges_storage(conn)
    conn.execute("create index if not exists idx_chunks_audio_time on audio_chunks(audio_file_id, start_ms, end_ms)")
    _ensure_column(conn, "speaker_mappings", "speaker_mapping_id", "text")
    _ensure_column(conn, "speaker_mappings", "speaker_cluster_id", "text")
    _ensure_column(conn, "speaker_mappings", "person_id", "text")
    _ensure_column(conn, "speaker_mappings", "confidence", "real")
    _ensure_column(conn, "speaker_mappings", "source", "text not null default 'speaker_review'")
    _ensure_column(conn, "speaker_mappings", "created_at", "text not null default ''")
    conn.execute(
        """
        update speaker_mappings
        set speaker_mapping_id = 'spmap_' || speaker
        where speaker_mapping_id is null or speaker_mapping_id = ''
        """
    )
    conn.execute("create index if not exists idx_speaker_mappings_cluster on speaker_mappings(speaker_cluster_id)")
    _ensure_column(conn, "segment_person_overrides", "person_id", "text")
    # Distinguish USER-LABELED ground truth ('manual') from AUTO-INFERRED voiceprint guesses
    # ('voiceprint'). Existing rows default to 'manual' so current attributions are treated as
    # confirmed labels and enrollment works immediately.
    _ensure_column(conn, "segment_person_overrides", "source", "text not null default 'manual'")
    _ensure_column(conn, "sessions", "primary_person_id", "text")
    # Fingerprint of the EFFECTIVE prompt used for the last successful session summary. Paired
    # with source_fingerprint (segments) by the summarize_session incremental skip: a prompt
    # edit (override or global template) must invalidate the skip even when segments are
    # unchanged. NULL (legacy rows) reads as not-fresh, forcing one regenerate that stamps it.
    _ensure_column(conn, "session_viewpoint_state", "summary_prompt_fingerprint", "text")
    # User-chosen session name surfaced in the 审核 list + 声纹 scope picker (nullable: most sessions
    # stay unnamed and show their time label instead).
    _ensure_column(conn, "sessions", "name", "text")
    conn.execute("create index if not exists idx_sessions_date on sessions(date_key, started_at)")
    conn.execute(
        """
        create table if not exists agent_sessions (
          agent_session_id text primary key,
          source_type text not null,
          source_path text not null,
          source_sha256 text not null,
          originator text,
          cli_version text,
          cwd text,
          model text,
          started_at text not null,
          ended_at text,
          title text,
          message_count integer not null default 0,
          tool_event_count integer not null default 0,
          created_at text not null,
          updated_at text not null
        )
        """
    )
    conn.execute("create index if not exists idx_agent_sessions_started on agent_sessions(started_at)")
    conn.execute(
        """
        create table if not exists agent_turns (
          agent_turn_id text primary key,
          agent_session_id text not null references agent_sessions(agent_session_id) on delete cascade,
          turn_index integer not null,
          role text not null,
          occurred_at text not null,
          text text not null,
          metadata_json text not null default '{}',
          created_at text not null
        )
        """
    )
    conn.execute("drop index if exists idx_agent_turns_session_index")
    conn.execute(
        "create unique index if not exists idx_agent_turns_session_index "
        "on agent_turns(agent_session_id, turn_index)"
    )
    conn.execute(
        """
        create table if not exists agent_tool_events (
          agent_tool_event_id text primary key,
          agent_session_id text not null references agent_sessions(agent_session_id) on delete cascade,
          event_index integer not null,
          occurred_at text not null,
          tool_name text not null,
          call_id text,
          arguments_json text not null default '{}',
          output_text text,
          status text not null,
          created_at text not null
        )
        """
    )
    conn.execute("drop index if exists idx_agent_tool_events_session_index")
    conn.execute(
        "create unique index if not exists idx_agent_tool_events_session_index "
        "on agent_tool_events(agent_session_id, event_index)"
    )
    _ensure_column(conn, "tasks", "priority", "integer not null default 100")
    _ensure_column(conn, "tasks", "retry_count", "integer not null default 0")
    _ensure_column(conn, "tasks", "max_retries", "integer not null default 3")
    _ensure_column(conn, "tasks", "lease_expires_at", "text")
    _ensure_column(conn, "tasks", "available_at", "text not null default ''")
    _ensure_column(conn, "tasks", "updated_at", "text not null default ''")
    conn.execute("update tasks set retry_count = attempt_count where retry_count = 0 and attempt_count > 0")
    conn.execute("update tasks set available_at = created_at where available_at = ''")
    conn.execute("update tasks set updated_at = created_at where updated_at = ''")
    # Matches claim_next_task's "where task_type/status ... order by priority, available_at" so the
    # claim scan stays index-ordered. The prior index was keyed (status, available_at, priority); a
    # same-named "create if not exists" would NOT replace it on an existing DB, so drop then recreate.
    conn.execute("drop index if exists idx_tasks_claim")
    conn.execute("create index if not exists idx_tasks_claim on tasks(task_type, status, priority, available_at)")
    conn.execute("create index if not exists idx_tasks_target on tasks(target_type, target_id)")
    # process_status_rows orders the full table by created_at on every SSE tick.
    conn.execute("create index if not exists idx_tasks_created on tasks(created_at)")
    # process_status_rows correlates transcript_segments.chunk_id = tasks.target_id per task
    # row; without this index each of those subqueries is a full segment-table scan.
    conn.execute("create index if not exists idx_segments_chunk on transcript_segments(chunk_id)")
    _ensure_column(conn, "memory_candidates", "source_type", "text not null default 'llm_daily_context'")
    _ensure_column(conn, "memory_candidates", "edited_claim", "text")
    _ensure_column(conn, "memory_candidates", "review_note_path", "text")
    _ensure_column(conn, "memory_candidates", "reviewed_at", "text")
    _ensure_column(conn, "memory_candidates", "created_card_id", "text")
    _ensure_column(conn, "memory_candidates", "date_key", "text")
    _ensure_column(conn, "memory_candidates", "normalized_claim_hash", "text")
    _ensure_column(conn, "memory_candidates", "prompt_version", "text not null default 'unknown'")
    _ensure_column(conn, "memory_candidates", "created_at", "text not null default ''")
    _ensure_column(conn, "memory_candidates", "updated_at", "text not null default ''")
    conn.execute("create index if not exists idx_candidates_status on memory_candidates(status)")
    _ensure_column(conn, "sessions", "exclude_from_memory", "integer not null default 0")
    _ensure_column(conn, "daily_reports", "date_key", "text")
    _ensure_column(conn, "daily_reports", "note_path", "text")
    _ensure_column(conn, "daily_reports", "total_recorded_ms", "integer not null default 0")
    _ensure_column(conn, "daily_reports", "active_speech_ms", "integer not null default 0")
    _ensure_column(conn, "daily_reports", "self_speech_ms", "integer not null default 0")
    _ensure_column(conn, "daily_reports", "others_speech_ms", "integer not null default 0")
    _ensure_column(conn, "daily_reports", "generated_at", "text")
    _ensure_column(conn, "daily_reports", "reviewed_at", "text")
    _ensure_column(conn, "daily_reports", "error", "text")
    _ensure_column(conn, "daily_reports", "created_at", "text not null default ''")
    daily_report_columns = {row["name"] for row in conn.execute("pragma table_info(daily_reports)").fetchall()}
    if "day" in daily_report_columns:
        conn.execute("update daily_reports set date_key = day where date_key is null and day is not null")
    conn.execute("create unique index if not exists idx_daily_reports_date_key on daily_reports(date_key)")
    _ensure_column(conn, "archive_records", "target_type", "text not null default ''")
    _ensure_column(conn, "archive_records", "target_id", "text not null default ''")
    _ensure_column(conn, "archive_records", "status", "text not null default 'verified'")
    _ensure_column(conn, "archive_records", "last_error", "text")
    _ensure_column(conn, "archive_records", "created_at", "text not null default ''")
    _ensure_column(conn, "archive_records", "updated_at", "text not null default ''")
    _relax_archive_records_audio_file_id(conn)
    conn.execute("update archive_records set target_type = 'audio_file' where target_type = ''")
    conn.execute("update archive_records set target_id = audio_file_id where target_id = ''")
    conn.execute(
        "create unique index if not exists idx_archive_records_target_archive on archive_records(target_type, target_id, archive_path)"
    )
    _ensure_column(conn, "evidence_refs", "source_ref", "text not null default ''")
    _ensure_column(conn, "evidence_refs", "owner_id", "text")
    _ensure_column(conn, "evidence_refs", "summary", "text")
    _ensure_column(conn, "evidence_refs", "created_at", "text not null default ''")
    conn.execute("update evidence_refs set source_ref = source_id where source_ref = ''")
    conn.execute("create unique index if not exists idx_evidence_refs_source_ref on evidence_refs(source_type, source_ref)")
    _ensure_column(conn, "memory_cards", "source_type", "text not null default 'confirmed_generated'")
    _ensure_column(conn, "memory_cards", "current_version", "integer not null default 1")
    _ensure_column(conn, "memory_cards", "owner_id", "text not null default ''")
    _ensure_column(conn, "memory_cards", "confidence", "real")
    _ensure_column(conn, "memory_cards", "observed_at", "text")
    _ensure_column(conn, "memory_cards", "valid_from", "text")
    _ensure_column(conn, "memory_cards", "valid_until", "text")
    _ensure_column(conn, "memory_cards", "visibility_json", "text not null default '{\"type\":\"private\"}'")
    _ensure_column(conn, "memory_cards", "tags_json", "text not null default '[]'")
    _ensure_column(conn, "memory_cards", "updated_at", "text not null default ''")
    conn.execute("create index if not exists idx_memory_cards_owner on memory_cards(owner_id, status)")
    conn.execute("create index if not exists idx_memory_cards_subject on memory_cards(claim_type, status)")
    conn.execute(
        "insert or ignore into schema_migrations (version, name) values (?, ?)",
        (1, "base_schema"),
    )
    conn.execute("drop view if exists v_segment_attribution")
    conn.execute(
        """
        create view v_segment_attribution as
        select
          ts.segment_id,
          coalesce(override.person_id, mapping.person_id) as person_id,
          case
            when override.person_id is not null then 'override'
            when mapping.person_id is not null then 'cluster_mapping'
          end as attribution_source
        from transcript_segments ts
        left join segment_person_overrides override on override.segment_id = ts.segment_id
        left join speaker_mappings mapping on mapping.speaker_cluster_id = coalesce(ts.speaker_cluster_id, ts.speaker)
        """
    )
    conn.commit()


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """Return all rows of the settings table as a key->value string map."""
    return {row["key"]: row["value"] for row in conn.execute("select key, value from settings").fetchall()}


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a single setting, stamping updated_at."""
    conn.execute(
        """
        insert into settings (key, value, updated_at)
        values (?, ?, current_timestamp)
        on conflict(key) do update set value = excluded.value, updated_at = current_timestamp
        """,
        (key, value),
    )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {definition}")


def _relax_audio_files_source_identity(conn: sqlite3.Connection) -> None:
    table = conn.execute("select sql from sqlite_master where type = 'table' and name = 'audio_files'").fetchone()
    if table is None or "unique(source_path, sha256)" not in str(table["sql"]).lower():
        return
    conn.execute("pragma foreign_keys = off")
    conn.execute("pragma legacy_alter_table = on")
    conn.execute("alter table audio_files rename to audio_files_legacy_identity")
    conn.execute(
        """
        create table audio_files (
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
          unique(source_path, source_size_bytes, source_mtime_ns, sha256)
        )
        """
    )
    conn.execute(
        """
        insert into audio_files (
          audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns,
          local_raw_path, sha256, duration_ms, recorded_at, imported_at, status
        )
        select
          audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns,
          local_raw_path, sha256, duration_ms, recorded_at, imported_at, status
        from audio_files_legacy_identity
        """
    )
    conn.execute("drop table audio_files_legacy_identity")
    conn.execute("pragma legacy_alter_table = off")
    conn.execute("pragma foreign_keys = on")


def _relax_archive_records_audio_file_id(conn: sqlite3.Connection) -> None:
    columns = {row["name"]: row for row in conn.execute("pragma table_info(archive_records)").fetchall()}
    audio_column = columns.get("audio_file_id")
    if audio_column is None or int(audio_column["notnull"]) == 0:
        return
    conn.execute("drop index if exists idx_archive_records_target_archive")
    conn.execute("alter table archive_records rename to archive_records_strict_audio")
    conn.execute(
        """
        create table archive_records (
          archive_record_id text primary key,
          target_type text not null default '',
          target_id text not null default '',
          audio_file_id text references audio_files(audio_file_id),
          source_path text not null,
          archive_path text not null,
          sha256 text not null,
          status text not null default 'verified',
          verified integer not null,
          archived_at text not null,
          last_error text,
          created_at text not null default '',
          updated_at text not null default ''
        )
        """
    )
    conn.execute(
        """
        insert into archive_records (
          archive_record_id, target_type, target_id, audio_file_id,
          source_path, archive_path, sha256, status, verified, archived_at,
          last_error, created_at, updated_at
        )
        select
          archive_record_id, target_type, target_id, audio_file_id,
          source_path, archive_path, sha256, status, verified, archived_at,
          last_error, created_at, updated_at
        from archive_records_strict_audio
        """
    )
    conn.execute("drop table archive_records_strict_audio")


def _remove_speech_ranges_storage(conn: sqlite3.Connection) -> None:
    audio_chunk_columns = {row["name"] for row in conn.execute("pragma table_info(audio_chunks)").fetchall()}
    if "speech_range_id" in audio_chunk_columns:
        conn.execute("drop index if exists idx_chunks_audio_time")
        conn.execute("alter table audio_chunks rename to audio_chunks_with_ranges")
        conn.execute(
            """
            create table audio_chunks (
              chunk_id text primary key,
              audio_file_id text not null references audio_files(audio_file_id),
              local_work_path text not null default '',
              start_ms integer not null default 0,
              end_ms integer not null default 0,
              absolute_start_at text,
              absolute_end_at text,
              vad_backend text,
              vad_config_json text,
              created_at text not null default '',
              source_start_ms integer not null,
              source_end_ms integer not null,
              local_chunk_path text not null,
              status text not null,
              unique(audio_file_id, source_start_ms, source_end_ms)
            )
            """
        )
        conn.execute(
            """
            insert into audio_chunks (
              chunk_id, audio_file_id, local_work_path, start_ms, end_ms,
              absolute_start_at, absolute_end_at, vad_backend, vad_config_json,
              created_at, source_start_ms, source_end_ms, local_chunk_path, status
            )
            select
              chunk_id, audio_file_id, local_work_path, start_ms, end_ms,
              absolute_start_at, absolute_end_at, vad_backend, vad_config_json,
              created_at, source_start_ms, source_end_ms, local_chunk_path, status
            from audio_chunks_with_ranges
            """
        )
        conn.execute("drop table audio_chunks_with_ranges")
    conn.execute("drop table if exists speech_ranges")
