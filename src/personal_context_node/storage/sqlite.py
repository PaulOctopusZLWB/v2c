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
  start_ms integer not null,
  end_ms integer not null,
  text text not null,
  language text not null,
  speaker text not null,
  evidence_id text not null unique
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

create table if not exists signed_events (
  event_id text primary key,
  event_type text not null,
  signer_did text not null,
  payload_json text not null,
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
