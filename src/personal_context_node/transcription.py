from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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
            select ac.chunk_id, ac.audio_file_id, ac.source_start_ms, ac.source_end_ms, ac.local_chunk_path
            from audio_chunks ac
            {where_clause}
            order by ac.source_start_ms
            """,
            params,
        )
        segments_created = 0
        for chunk in chunks:
            conn.execute(
                "update transcript_segments set is_active = 0 where audio_file_id = ? and is_active = 1",
                (chunk["audio_file_id"],),
            )
            asr_run_id = f"asrrun_{uuid4().hex}"
            chunk_path = config.data_dir / chunk["local_chunk_path"]
            for segment in asr.transcribe(chunk_path):
                absolute_start_ms = chunk["source_start_ms"] + segment.start_ms
                absolute_end_ms = min(chunk["source_start_ms"] + segment.end_ms, chunk["source_end_ms"])
                conn.execute(
                    """
                    insert into transcript_segments (
                      segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
                      language, speaker, evidence_id, confidence, asr_backend,
                      model_name, model_version, asr_run_id, is_active, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"seg_{uuid4().hex}",
                        chunk["audio_file_id"],
                        chunk["chunk_id"],
                        absolute_start_ms,
                        absolute_end_ms,
                        segment.text,
                        segment.language,
                        "self",
                        f"ev_seg_{uuid4().hex}",
                        segment.confidence,
                        asr.__class__.__name__,
                        asr.model_name,
                        asr.model_version,
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
        "confidence": "alter table transcript_segments add column confidence real",
        "asr_backend": "alter table transcript_segments add column asr_backend text not null default 'mock_first_milestone'",
        "model_name": "alter table transcript_segments add column model_name text not null default 'mock'",
        "model_version": "alter table transcript_segments add column model_version text not null default 'mock'",
        "asr_run_id": "alter table transcript_segments add column asr_run_id text",
        "is_active": "alter table transcript_segments add column is_active integer not null default 1",
        "created_at": "alter table transcript_segments add column created_at text not null default ''",
    }
    for column, sql in migrations.items():
        if column not in existing:
            conn.execute(sql)
    conn.commit()
