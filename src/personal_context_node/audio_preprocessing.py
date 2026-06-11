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
    chunk_overlap_ms: int = 0,
    audio_file_id: str | None = None,
) -> PreprocessResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        where_clause = "where audio_file_id not in (select distinct audio_file_id from audio_chunks)"
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
            vad_result = vad.detect(local_raw_path)
            for speech_range in vad_result.ranges:
                ranges_created += 1
                for chunk_range in _split_range(speech_range, max_chunk_ms=max_chunk_ms, chunk_overlap_ms=chunk_overlap_ms):
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
                          chunk_id, audio_file_id, local_work_path,
                          start_ms, end_ms, absolute_start_at, absolute_end_at,
                          vad_backend, vad_config_json, created_at,
                          source_start_ms, source_end_ms, local_chunk_path, status
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"chk_{uuid4().hex}",
                            audio_file["audio_file_id"],
                            str(chunk_path),
                            chunk_range.start_ms,
                            chunk_range.end_ms,
                            absolute_start_at,
                            absolute_end_at,
                            vad_result.backend,
                            json.dumps(vad_result.config, ensure_ascii=False, sort_keys=True),
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


def _split_range(speech_range: SpeechRange, *, max_chunk_ms: int, chunk_overlap_ms: int = 0) -> list[SpeechRange]:
    if chunk_overlap_ms < 0:
        raise ValueError("chunk_overlap_ms must be non-negative")
    if chunk_overlap_ms >= max_chunk_ms:
        raise ValueError("chunk_overlap_ms must be smaller than max_chunk_ms")
    chunks: list[SpeechRange] = []
    cursor = speech_range.start_ms
    while cursor < speech_range.end_ms:
        end_ms = min(cursor + max_chunk_ms, speech_range.end_ms)
        chunks.append(SpeechRange(start_ms=cursor, end_ms=end_ms))
        if end_ms == speech_range.end_ms:
            break
        cursor = end_ms - chunk_overlap_ms
    return chunks


def _write_chunk(*, config: AppConfig, source_path: Path, recorded_day: str, start_ms: int, end_ms: int) -> Path:
    if config.audio.target_sample_format != "s16":
        raise ValueError(f"unsupported target sample format: {config.audio.target_sample_format}")
    chunk_dir = config.work_audio_dir / recorded_day
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"{source_path.stem}_{start_ms:09d}_{end_ms:09d}.wav"
    with wave.open(str(source_path), "rb") as source:
        sample_rate = source.getframerate()
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        start_frame = int(start_ms * sample_rate / 1000)
        frame_count = int((end_ms - start_ms) * sample_rate / 1000)
        source.setpos(start_frame)
        frames = source.readframes(frame_count)
        frames = _convert_pcm_frames(
            frames,
            source_sample_rate=sample_rate,
            source_channels=channels,
            source_sample_width=sample_width,
            target_sample_rate=config.audio.target_sample_rate_hz,
            target_channels=config.audio.target_channels,
            target_sample_width=2,
        )
        with wave.open(str(chunk_path), "wb") as target:
            target.setnchannels(config.audio.target_channels)
            target.setsampwidth(2)
            target.setframerate(config.audio.target_sample_rate_hz)
            target.writeframes(frames)
    return chunk_path


def _convert_pcm_frames(
    frames: bytes,
    *,
    source_sample_rate: int,
    source_channels: int,
    source_sample_width: int,
    target_sample_rate: int,
    target_channels: int,
    target_sample_width: int,
) -> bytes:
    if target_sample_width != 2:
        raise ValueError(f"unsupported target sample width: {target_sample_width}")
    if source_channels not in (1, 2) or target_channels not in (1, 2):
        raise ValueError(f"unsupported channel conversion: {source_channels} -> {target_channels}")

    source_frames = _decode_pcm_frames(frames, sample_width=source_sample_width, channels=source_channels)
    if not source_frames:
        return b""
    target_frame_count = max(1, round(len(source_frames) * target_sample_rate / source_sample_rate))
    converted = bytearray()
    for target_index in range(target_frame_count):
        source_index = min(len(source_frames) - 1, int(target_index * source_sample_rate / target_sample_rate))
        samples = source_frames[source_index]
        if target_channels == 1:
            output_samples = [round(sum(samples) / len(samples))]
        elif len(samples) == 1:
            output_samples = [samples[0], samples[0]]
        else:
            output_samples = samples[:2]
        for sample in output_samples:
            converted.extend(_to_s16(sample))
    return bytes(converted)


def _decode_pcm_frames(frames: bytes, *, sample_width: int, channels: int) -> list[list[int]]:
    frame_width = sample_width * channels
    decoded: list[list[int]] = []
    for frame_start in range(0, len(frames) - frame_width + 1, frame_width):
        samples: list[int] = []
        for channel in range(channels):
            sample_start = frame_start + channel * sample_width
            raw = frames[sample_start : sample_start + sample_width]
            sample = int.from_bytes(raw, byteorder="little", signed=True)
            if sample_width > 2:
                sample >>= (sample_width - 2) * 8
            elif sample_width < 2:
                sample <<= (2 - sample_width) * 8
            samples.append(sample)
        decoded.append(samples)
    return decoded


def _to_s16(sample: int) -> bytes:
    clipped = max(-(2**15), min(2**15 - 1, sample))
    return clipped.to_bytes(2, byteorder="little", signed=True)


def _absolute_time(recorded_at: str, offset_ms: int) -> str:
    return (datetime.fromisoformat(recorded_at) + timedelta(milliseconds=offset_ms)).isoformat()
