from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRPort
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class TranscriptionResult:
    chunks_transcribed: int
    segments_created: int


def transcribe_pending_chunks(*, config: AppConfig, asr: ASRPort, chunk_id: str | None = None) -> TranscriptionResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _ensure_transcript_columns(conn)
        where_clause = "where ac.status = 'pending_asr'"
        params: tuple[object, ...] = ()
        if chunk_id is not None:
            where_clause += " and ac.chunk_id = ?"
            params = (chunk_id,)
        chunks = fetch_all(
            conn,
            f"""
            select ac.chunk_id, ac.audio_file_id, ac.source_start_ms, ac.source_end_ms, ac.local_chunk_path, af.recorded_at
            from audio_chunks ac
            join audio_files af on af.audio_file_id = ac.audio_file_id
            {where_clause}
            order by ac.source_start_ms
            """,
            params,
        )
        segments_created = 0
        for chunk in chunks:
            audio_file_id = str(chunk["audio_file_id"])
            # Retire THIS chunk's prior segments plus any of the file's active segments
            # not backed by a real audio_chunk (e.g. mock-milestone placeholders).
            # Per-chunk scoping is required: deactivating the whole file on every
            # per-chunk ASR task would drop all but the last chunk of a multi-chunk
            # recording (§36.2.5). Chunk boundaries are stable (preprocess is one-time
            # per file), so sibling chunks keep their latest-run segments.
            conn.execute(
                """
                update transcript_segments set is_active = 0
                where is_active = 1
                  and audio_file_id = ?
                  and (
                    chunk_id = ?
                    or chunk_id not in (select chunk_id from audio_chunks where audio_file_id = ?)
                  )
                """,
                (audio_file_id, chunk["chunk_id"], audio_file_id),
            )
            asr_run_id = f"asrrun_{uuid4().hex}"
            # local_chunk_path is already the full work path (work_audio_dir/...), as
            # written by VAD; use it directly (consistent with local_raw_path). Re-
            # prefixing config.data_dir would double a relative data_dir (the §32 default).
            chunk_path = Path(str(chunk["local_chunk_path"]))
            asr_result = asr.transcribe(chunk_path)
            decode_config_json = json.dumps(asr_result.decode_config, ensure_ascii=False, sort_keys=True)
            for segment in asr_result.segments:
                absolute_start_ms = chunk["source_start_ms"] + segment.start_ms
                absolute_end_ms = min(chunk["source_start_ms"] + segment.end_ms, chunk["source_end_ms"])
                absolute_start_at = _absolute_time(str(chunk["recorded_at"]), int(absolute_start_ms))
                absolute_end_at = _absolute_time(str(chunk["recorded_at"]), int(absolute_end_ms))
                speaker_cluster_id = "self"
                conn.execute(
                    """
                    insert into transcript_segments (
                      segment_id, audio_file_id, chunk_id, start_ms, end_ms,
                      absolute_start_at, absolute_end_at, text,
                      language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend,
                      model_name, model_version, decode_config_json, asr_tags_json,
                      asr_run_id, is_active, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"seg_{uuid4().hex}",
                        chunk["audio_file_id"],
                        chunk["chunk_id"],
                        absolute_start_ms,
                        absolute_end_ms,
                        absolute_start_at,
                        absolute_end_at,
                        segment.text,
                        segment.language,
                        speaker_cluster_id,
                        speaker_cluster_id,
                        f"ev_seg_{uuid4().hex}",
                        segment.confidence,
                        asr_result.backend,
                        asr_result.model_name,
                        asr_result.model_version,
                        decode_config_json,
                        json.dumps(segment.tags, ensure_ascii=False, sort_keys=True),
                        asr_run_id,
                        1,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                segments_created += 1
            conn.execute("update audio_chunks set status = 'transcribed' where chunk_id = ?", (chunk["chunk_id"],))
        conn.commit()
        return TranscriptionResult(chunks_transcribed=len(chunks), segments_created=segments_created)
    finally:
        conn.close()


def _ensure_transcript_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(transcript_segments)").fetchall()}
    migrations = {
        "chunk_id": "alter table transcript_segments add column chunk_id text",
        "absolute_start_at": "alter table transcript_segments add column absolute_start_at text",
        "absolute_end_at": "alter table transcript_segments add column absolute_end_at text",
        "speaker_cluster_id": "alter table transcript_segments add column speaker_cluster_id text",
        "confidence": "alter table transcript_segments add column confidence real",
        "asr_backend": "alter table transcript_segments add column asr_backend text not null default 'mock_first_milestone'",
        "model_name": "alter table transcript_segments add column model_name text not null default 'mock'",
        "model_version": "alter table transcript_segments add column model_version text not null default 'mock'",
        "decode_config_json": "alter table transcript_segments add column decode_config_json text",
        "asr_tags_json": "alter table transcript_segments add column asr_tags_json text not null default '[]'",
        "asr_run_id": "alter table transcript_segments add column asr_run_id text",
        "is_active": "alter table transcript_segments add column is_active integer not null default 1",
        "created_at": "alter table transcript_segments add column created_at text not null default ''",
    }
    for column, sql in migrations.items():
        if column not in existing:
            conn.execute(sql)
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
    conn.commit()


def _absolute_time(recorded_at: str, offset_ms: int) -> str:
    return (datetime.fromisoformat(recorded_at) + timedelta(milliseconds=offset_ms)).isoformat()
