from __future__ import annotations

import json
import sqlite3
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.vad import SpeechRange, VADPort
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PreprocessResult:
    audio_files_processed: int
    speech_ranges_created: int
    audio_chunks_created: int


def preprocess_imported_audio(
    *,
    config: AppConfig,
    vad: VADPort,
    max_chunk_ms: int = 30_000,
    audio_file_id: str | None = None,
) -> PreprocessResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        where_clause = "where audio_file_id not in (select distinct audio_file_id from speech_ranges)"
        params: tuple[object, ...] = ()
        if audio_file_id is not None:
            where_clause += " and audio_file_id = ?"
            params = (audio_file_id,)
        files = fetch_all(
            conn,
            f"""
            select audio_file_id, local_raw_path, recorded_at
            from audio_files
            {where_clause}
            order by imported_at
            """,
            params,
        )
        ranges_created = 0
        chunks_created = 0
        for audio_file in files:
            local_raw_path = Path(audio_file["local_raw_path"])
            speech_ranges = vad.detect(local_raw_path)
            for speech_range in speech_ranges:
                range_id = f"rng_{uuid4().hex}"
                conn.execute(
                    """
                    insert into speech_ranges (
                      speech_range_id, audio_file_id, start_ms, end_ms, vad_backend
                    ) values (?, ?, ?, ?, ?)
                    """,
                    (
                        range_id,
                        audio_file["audio_file_id"],
                        speech_range.start_ms,
                        speech_range.end_ms,
                        vad.__class__.__name__,
                    ),
                )
                ranges_created += 1
                for chunk_range in _split_range(speech_range, max_chunk_ms=max_chunk_ms):
                    created_at = datetime.now(timezone.utc).isoformat()
                    absolute_start_at = _absolute_time(str(audio_file["recorded_at"]), chunk_range.start_ms)
                    absolute_end_at = _absolute_time(str(audio_file["recorded_at"]), chunk_range.end_ms)
                    chunk_path = _write_chunk(
                        config=config,
                        source_path=local_raw_path,
                        recorded_day=audio_file["recorded_at"][:10],
                        start_ms=chunk_range.start_ms,
                        end_ms=chunk_range.end_ms,
                    )
                    conn.execute(
                        """
                        insert into audio_chunks (
                          chunk_id, audio_file_id, speech_range_id, local_work_path,
                          start_ms, end_ms, absolute_start_at, absolute_end_at,
                          vad_backend, vad_config_json, created_at,
                          source_start_ms, source_end_ms, local_chunk_path, status
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"chk_{uuid4().hex}",
                            audio_file["audio_file_id"],
                            range_id,
                            str(chunk_path),
                            chunk_range.start_ms,
                            chunk_range.end_ms,
                            absolute_start_at,
                            absolute_end_at,
                            vad.__class__.__name__,
                            _vad_config_json(vad),
                            created_at,
                            chunk_range.start_ms,
                            chunk_range.end_ms,
                            str(chunk_path),
                            "pending_asr",
                        ),
                    )
                    chunks_created += 1
        conn.commit()
        return PreprocessResult(
            audio_files_processed=len(files),
            speech_ranges_created=ranges_created,
            audio_chunks_created=chunks_created,
        )
    finally:
        conn.close()


def _split_range(speech_range: SpeechRange, *, max_chunk_ms: int) -> list[SpeechRange]:
    chunks: list[SpeechRange] = []
    cursor = speech_range.start_ms
    while cursor < speech_range.end_ms:
        end_ms = min(cursor + max_chunk_ms, speech_range.end_ms)
        chunks.append(SpeechRange(start_ms=cursor, end_ms=end_ms))
        cursor = end_ms
    return chunks


def _write_chunk(*, config: AppConfig, source_path: Path, recorded_day: str, start_ms: int, end_ms: int) -> Path:
    chunk_dir = config.work_audio_dir / recorded_day
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"{source_path.stem}_{start_ms:09d}_{end_ms:09d}.wav"
    with wave.open(str(source_path), "rb") as source:
        sample_rate = source.getframerate()
        start_frame = int(start_ms * sample_rate / 1000)
        frame_count = int((end_ms - start_ms) * sample_rate / 1000)
        source.setpos(start_frame)
        frames = source.readframes(frame_count)
        with wave.open(str(chunk_path), "wb") as target:
            target.setnchannels(source.getnchannels())
            target.setsampwidth(source.getsampwidth())
            target.setframerate(sample_rate)
            target.writeframes(frames)
    return chunk_path


def _absolute_time(recorded_at: str, offset_ms: int) -> str:
    return (datetime.fromisoformat(recorded_at) + timedelta(milliseconds=offset_ms)).isoformat()


def _vad_config_json(vad: VADPort) -> str:
    config = {
        key: value
        for key, value in vars(vad).items()
        if isinstance(value, str | int | float | bool | type(None))
    }
    return json.dumps(config, ensure_ascii=False, sort_keys=True)
