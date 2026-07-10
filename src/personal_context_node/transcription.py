from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.audio_preprocessing import _read_wav_metadata
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.errors import TerminalPortError
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


def transcribe_audio_file_diarized(*, config: AppConfig, asr: ASRPort, audio_file_id: str) -> TranscriptionResult:
    """Per-file diarized ASR sibling of transcribe_pending_chunks (§ whole-file diarization).

    Runs ONCE per audio_file. ASR segments already carry ABSOLUTE source-file
    start_ms/end_ms and a `.speaker` diarization cluster label ("spk_01", … or "self").
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _ensure_transcript_columns(conn)
        rows = fetch_all(
            conn,
            "select audio_file_id, local_raw_path, recorded_at, duration_ms from audio_files where audio_file_id = ?",
            (audio_file_id,),
        )
        if not rows:
            return TranscriptionResult(chunks_transcribed=0, segments_created=0)
        audio = rows[0]
        if int(audio["duration_ms"] or 0) <= 0:
            raise TerminalPortError(f"empty audio file has no frames: {audio_file_id}")
        recorded_at = str(audio["recorded_at"])
        # Whole-file scope: this runs once per file (segments are already in absolute
        # source-file time), so retire ALL of this file's active segments before reinsert
        # — re-run safe (deactivate-then-reinsert), no per-chunk scoping needed.
        conn.execute(
            "update transcript_segments set is_active = 0 where is_active = 1 and audio_file_id = ?",
            (audio_file_id,),
        )
        asr_run_id = f"asrrun_{uuid4().hex}"
        # local_raw_path is the already-rooted raw path (mirrors transcribe_pending_chunks'
        # local_chunk_path handling — do NOT re-prefix config.data_dir).
        asr_result = asr.transcribe(Path(str(audio["local_raw_path"])))
        if not asr_result.segments:
            raise TerminalPortError(f"ASR produced no transcript segments for audio file: {audio_file_id}")
        decode_config_json = json.dumps(asr_result.decode_config, ensure_ascii=False, sort_keys=True)
        now = datetime.now(timezone.utc).isoformat()
        segments_created = 0
        clusters_upserted: set[str] = set()
        for segment in asr_result.segments:
            absolute_start_ms = int(segment.start_ms)
            absolute_start_at = _absolute_time(recorded_at, absolute_start_ms)
            absolute_end_at = _absolute_time(recorded_at, int(segment.end_ms))
            # The diarized path has no per-VAD audio_chunk, so synthesize a chunk_id
            # (transcript_segments.chunk_id is NOT NULL). It MUST be per-segment and
            # deterministic from the segment's absolute start ms: distinct so that an
            # internal silence gap splitting one file into multiple sessions gives each
            # session a DISTINCT first-chunk_id (the session-id reuse anchor in
            # sessions.py keys on first_chunk_id — a single file-wide chunk_id collapses
            # that anchor and mints fresh ses_* ids on every re-run); and deterministic
            # (the diarizer is deterministic → same audio → same start_ms → same
            # chunk_id) so the anchor matches across ASR re-runs. NOT a uuid.
            chunk_id = f"diar_{audio_file_id}_{absolute_start_ms:09d}"
            # speaker and speaker_cluster_id MUST stay equal: the review path joins on
            # `speaker`, the attribution view on `speaker_cluster_id`. Do NOT diverge them.
            speaker_cluster_id = segment.speaker
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
                    audio_file_id,
                    chunk_id,
                    segment.start_ms,
                    segment.end_ms,
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
                    now,
                ),
            )
            segments_created += 1
            # Upsert a speaker_clusters row for each DISTINCT non-"self" label. "self"
            # gets NO cluster row (preserves the single-owner default; lazy-cluster
            # behavior covers self), mirroring _upsert_speaker_cluster's insert shape.
            if speaker_cluster_id != "self" and speaker_cluster_id not in clusters_upserted:
                conn.execute(
                    """
                    insert into speaker_clusters (speaker_cluster_id, label, source_type, source_ref, created_at)
                    values (?, ?, ?, ?, ?)
                    on conflict(speaker_cluster_id) do nothing
                    """,
                    (speaker_cluster_id, speaker_cluster_id, "diarization", audio_file_id, now),
                )
                clusters_upserted.add(speaker_cluster_id)
        conn.commit()
        return TranscriptionResult(chunks_transcribed=1, segments_created=segments_created)
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


# Segment ids are minted as "seg_<hex>" (see _segment_id / diarized inserts). The cache
# filename below is derived from the URL-supplied segment_id, so we only ever materialize a
# slice for an id matching this safe pattern — defense-in-depth against path traversal even
# though the DB has already confirmed an active segment with this exact id.
_SAFE_SEGMENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_safe_segment_id(segment_id: str) -> bool:
    return bool(_SAFE_SEGMENT_ID.fullmatch(segment_id)) and os.sep not in segment_id and ".." not in segment_id


def _write_wav_header(out, *, audio_format: int, channels: int, sample_rate: int, bits_per_sample: int, data_size: int) -> None:
    """Write a canonical 44-byte RIFF/WAVE header with a 16-byte fmt chunk, preserving the
    source's audio_format (1=PCM incl. 24-bit, 3=IEEE float) so the browser decodes it as-is."""
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    out.write(b"RIFF")
    out.write(struct.pack("<I", 36 + data_size))
    out.write(b"WAVE")
    out.write(b"fmt ")
    out.write(struct.pack("<IHHIIHH", 16, audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample))
    out.write(b"data")
    out.write(struct.pack("<I", data_size))


def _slice_wav(source: Path, dest: Path, start_ms: int, end_ms: int) -> Path | None:
    """Slice [start_ms, end_ms] out of an uncompressed WAV into a new standalone WAV at ``dest``.

    Works for every WAV the rest of the pipeline ingests — PCM (including 24-bit) AND IEEE-float
    (audio_format 3) — by copying the source fmt verbatim and slicing raw frame bytes. (stdlib
    ``wave`` can't even *open* a float WAV: ``unknown format: 3``; this dataset has one such
    source.) Reads ONLY the requested byte range via seek (never the whole, possibly hundreds-of-
    MB, source into memory). Writes to a temp path then os.replace()s into place so a concurrent
    reader never observes a half-written file (atomic). Returns None on a zero-length window or any
    parse/IO error (missing/compressed/corrupt source) so the caller yields a graceful 404 instead
    of a 500 or an undecodable empty WAV (which would also poison the cache permanently).
    """
    try:
        meta = _read_wav_metadata(source)
        channels = int(meta["channels"])
        sample_rate = int(meta["sample_rate"])
        bits = int(meta["bits_per_sample"])
        audio_format = int(meta["audio_format"])
        data_offset = int(meta["data_offset"])
        data_size = int(meta["data_size"])
        block_align = channels * bits // 8
        if block_align <= 0 or sample_rate <= 0:
            return None
        total_frames = data_size // block_align
        start_frame = max(0, min(start_ms * sample_rate // 1000, total_frames))
        end_frame = max(start_frame, min(end_ms * sample_rate // 1000, total_frames))
        if end_frame <= start_frame:
            return None  # zero-length window -> graceful 404, never an undecodable empty WAV
        with source.open("rb") as handle:
            handle.seek(data_offset + start_frame * block_align)
            frames = handle.read((end_frame - start_frame) * block_align)
        frames = frames[: (len(frames) // block_align) * block_align]  # whole frames only
        if not frames:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"{dest.name}.{uuid4().hex}.tmp")
        try:
            with tmp.open("wb") as out:
                _write_wav_header(
                    out, audio_format=audio_format, channels=channels,
                    sample_rate=sample_rate, bits_per_sample=bits, data_size=len(frames),
                )
                out.write(frames)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    except (ValueError, struct.error, OSError):
        return None
    return dest


def segment_audio_path(*, config: AppConfig, segment_id: str) -> Path | None:
    # Reject unsafe ids up front: segment_id arrives from the URL and is later used to build the
    # slice cache filename, so this guards path traversal independent of the DB lookup below.
    if not _is_safe_segment_id(segment_id):
        return None
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select ac.local_chunk_path
            from transcript_segments ts
            join audio_chunks ac on ac.chunk_id = ts.chunk_id
            where ts.segment_id = ? and ts.is_active = 1
            """,
            (segment_id,),
        )
        if rows and rows[0]["local_chunk_path"]:
            # local_chunk_path is stored as the already-rooted work path (work_audio_dir/...),
            # exactly as transcribe_pending_chunks reads it — do NOT re-prefix config.data_dir
            # (that would double a relative data_dir; see the §32 note in transcribe_pending_chunks).
            chunk_path = Path(str(rows[0]["local_chunk_path"]))
            if chunk_path.exists():
                return chunk_path

        # Diarize mode is whole-file and writes NO audio_chunks rows; its segments carry a
        # synthetic chunk_id with no matching chunk. Fall back to slicing the source raw wav
        # over the segment's absolute [start_ms, end_ms] window.
        fallback = fetch_all(
            conn,
            """
            select ts.start_ms, ts.end_ms, af.local_raw_path
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            where ts.segment_id = ? and ts.is_active = 1
            """,
            (segment_id,),
        )
    finally:
        conn.close()

    if not fallback or not fallback[0]["local_raw_path"]:
        return None
    source = Path(str(fallback[0]["local_raw_path"]))
    if not source.exists():
        return None
    cache_path = config.data_dir / "audio" / "segments" / f"{segment_id}.wav"
    if cache_path.exists():
        return cache_path  # idempotent reuse — no re-slice
    return _slice_wav(source, cache_path, int(fallback[0]["start_ms"]), int(fallback[0]["end_ms"]))
