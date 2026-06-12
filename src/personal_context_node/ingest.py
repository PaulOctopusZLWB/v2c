from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.file_import import FileImportPort, ImportedRawAudio
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.tasks import enqueue_task_in_conn


@dataclass(frozen=True)
class IngestScanResult:
    files: list[Path]


@dataclass(frozen=True)
class IngestImportResult:
    imported_files: int


@dataclass(frozen=True)
class WavMetadataRepairResult:
    scanned_files: int
    repaired_files: int
    skipped_files: int


@dataclass(frozen=True)
class FileSnapshot:
    size_bytes: int
    mtime_ns: int


def scan_audio_files(*, source_dir: Path) -> IngestScanResult:
    return IngestScanResult(files=_iter_audio_paths(source_dir=source_dir, recursive=False))


def scan_audio_files_recursive(*, source_dir: Path) -> IngestScanResult:
    return IngestScanResult(files=_iter_audio_paths(source_dir=source_dir, recursive=True))


def repair_bwf_metadata_in_source_dir(*, source_dir: Path, recursive: bool = False, dry_run: bool = False) -> WavMetadataRepairResult:
    scanned_files = 0
    repaired_files = 0
    skipped_files = 0
    for path in _iter_audio_paths(source_dir=source_dir, recursive=recursive):
        scanned_files += 1
        expected_recorded_at = _recorded_at_from_name_or_none(path)
        if expected_recorded_at is None:
            skipped_files += 1
            continue
        if _repair_wav_file_metadata(path, expected_recorded_at, dry_run=dry_run):
            repaired_files += 1
    return WavMetadataRepairResult(
        scanned_files=scanned_files,
        repaired_files=repaired_files,
        skipped_files=skipped_files,
    )


def import_audio_files(*, config: AppConfig, source_dir: Path) -> IngestImportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = import_audio_files_in_conn(conn, config=config, source_dir=source_dir)
        conn.commit()
        return IngestImportResult(imported_files=imported)
    finally:
        conn.close()


def import_audio_files_from_port(*, config: AppConfig, importer: FileImportPort) -> IngestImportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = import_audio_files_from_port_in_conn(conn, config=config, importer=importer)
        conn.commit()
        return IngestImportResult(imported_files=imported)
    finally:
        conn.close()


def import_audio_files_in_conn(conn: sqlite3.Connection, *, config: AppConfig, source_dir: Path) -> int:
    imported = 0
    for source_path in scan_audio_files(source_dir=source_dir).files:
        if not is_file_stable(source_path):
            continue
        source_stat = source_path.stat()
        sha256 = _sha256(source_path)
        existing = conn.execute(
            """
            select 1
            from audio_files
            where source_path = ?
              and source_size_bytes = ?
              and source_mtime_ns = ?
              and sha256 = ?
            """,
            (str(source_path), source_stat.st_size, source_stat.st_mtime_ns, sha256),
        ).fetchone()
        if existing:
            continue
        recorded_at = _recorded_at_from_name(path=source_path)
        recorded_date = recorded_at[:10]
        local_dir = config.raw_audio_dir / recorded_date
        local_dir.mkdir(parents=True, exist_ok=True)
        audio_file_id = f"aud_{uuid4().hex}"
        local_path = _raw_store_path(local_dir=local_dir, source_path=source_path, audio_file_id=audio_file_id)
        shutil.copy2(source_path, local_path)
        _repair_wav_file_metadata(local_path, recorded_at)
        mark_raw_evidence_read_only(local_path)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns,
              local_raw_path, sha256, duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audio_file_id,
                config.source_device,
                str(source_path),
                source_stat.st_size,
                source_stat.st_mtime_ns,
                str(local_path),
                sha256,
                _duration_ms(local_path),
                recorded_at,
                datetime.now(timezone.utc).isoformat(),
                "imported",
            ),
        )
        enqueue_task_in_conn(conn, task_type="vad", target_type="audio_file", target_id=audio_file_id)
        imported += 1
    return imported


def import_audio_files_from_port_in_conn(conn: sqlite3.Connection, *, config: AppConfig, importer: FileImportPort) -> int:
    imported = 0
    for device in importer.discover_devices():
        for source in importer.discover_audio_files(device):
            stable_source = importer.wait_until_stable(source, stable_seconds=config.dji_mic_3.stable_seconds)
            raw_audio = importer.copy_to_raw_store(stable_source, config.raw_audio_dir)
            if _raw_audio_exists(conn, raw_audio):
                continue
            mark_raw_evidence_read_only(raw_audio.local_raw_path)
            audio_file_id = f"aud_{uuid4().hex}"
            conn.execute(
                """
                insert into audio_files (
                  audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns,
                  local_raw_path, sha256, duration_ms, recorded_at, imported_at, status
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audio_file_id,
                    source.device.label,
                    str(source.source_path),
                    source.size_bytes,
                    source.mtime_ns,
                    str(raw_audio.local_raw_path),
                    raw_audio.sha256,
                    raw_audio.duration_ms,
                    raw_audio.recorded_at,
                    datetime.now(timezone.utc).isoformat(),
                    "imported",
                ),
            )
            enqueue_task_in_conn(conn, task_type="vad", target_type="audio_file", target_id=audio_file_id)
            imported += 1
    return imported


def _raw_audio_exists(conn: sqlite3.Connection, raw_audio: ImportedRawAudio) -> bool:
    source = raw_audio.source.source
    existing = conn.execute(
        """
        select 1
        from audio_files
        where source_path = ?
          and source_size_bytes = ?
          and source_mtime_ns = ?
          and sha256 = ?
        """,
        (str(source.source_path), source.size_bytes, source.mtime_ns, raw_audio.sha256),
    ).fetchone()
    return existing is not None


def _raw_store_path(*, local_dir: Path, source_path: Path, audio_file_id: str) -> Path:
    target = local_dir / source_path.name
    if not target.exists():
        return target
    return local_dir / f"{source_path.stem}_{audio_file_id}{source_path.suffix}"


def mark_raw_evidence_read_only(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def is_file_stable(path: Path, *, settle_seconds: float = 0.1) -> bool:
    first = _file_snapshot(path)
    time.sleep(settle_seconds)
    return first == _file_snapshot(path)


def _file_snapshot(path: Path) -> FileSnapshot:
    stat = path.stat()
    return FileSnapshot(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as wav:
            return round(wav.getnframes() / wav.getframerate() * 1000)
    except wave.Error:
        return _duration_ms_from_riff_chunks(path)


def _duration_ms_from_riff_chunks(path: Path) -> int:
    sample_rate: int | None = None
    block_align: int | None = None
    data_bytes = 0
    with path.open("rb") as source_file:
        if source_file.read(4) != b"RIFF":
            raise wave.Error("not a RIFF file")
        source_file.seek(8)
        if source_file.read(4) != b"WAVE":
            raise wave.Error("not a WAVE file")
        while True:
            header = source_file.read(8)
            if len(header) < 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", header)
            chunk_data = source_file.read(chunk_size)
            if len(chunk_data) < chunk_size:
                raise wave.Error("truncated WAVE chunk")
            if chunk_size % 2 == 1:
                source_file.seek(1, os.SEEK_CUR)
            if chunk_id == b"fmt " and len(chunk_data) >= 16:
                _, _, sample_rate, _, block_align, _ = struct.unpack("<HHIIHH", chunk_data[:16])
            elif chunk_id == b"data":
                data_bytes += chunk_size
    if not sample_rate or not block_align:
        raise wave.Error("missing WAVE fmt chunk")
    frames = data_bytes / block_align
    return round(frames / sample_rate * 1000)


def _recorded_at_from_name(path: Path) -> str:
    parsed = _recorded_at_from_name_or_none(path=path)
    if parsed:
        return parsed
    now = datetime.now().astimezone()
    return f"{now.date().isoformat()}T{now.time().isoformat(timespec='seconds')}+{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}"


def _recorded_at_from_name_or_none(path: Path) -> str | None:
    match = re.search(r"_(\d{8})_(\d{6})_", path.name)
    if not match:
        return None
    raw_date = match.group(1)
    raw_time = match.group(2)
    year = int(raw_date[:4])
    month = int(raw_date[4:6])
    day = int(raw_date[6:8])

    if year == 2087 and month == 5:
        # DJI Mic sample data in this repository uses a broken year/month encoding.
        # 2087-05-* should map to 2025-06-*.
        year = 2025
        month += 1

    return f"{year:04d}-{month:02d}-{day:02d}T{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:6]}+08:00"


def _iter_audio_paths(*, source_dir: Path, recursive: bool) -> list[Path]:
    candidates = source_dir.rglob("*.wav") if recursive else source_dir.iterdir()
    return sorted(path for path in candidates if path.is_file() and path.suffix.lower() == ".wav")


def _repair_wav_file_metadata(path: Path, recorded_at: str, *, dry_run: bool = False) -> bool:
    expected_date = recorded_at[:10]
    expected_time = recorded_at[11:19]
    chunks: list[tuple[bytes, bytes]] = []
    has_repair = False

    with path.open("rb") as source_file:
        if source_file.read(4) != b"RIFF":
            return False
        source_file.seek(8)
        if source_file.read(4) != b"WAVE":
            return False
        while True:
            header = source_file.read(8)
            if len(header) < 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", header)
            chunk_data = source_file.read(chunk_size)
            if len(chunk_data) < chunk_size:
                return False
            if chunk_size % 2 == 1:
                source_file.seek(1, os.SEEK_CUR)

            rewritten_chunk, chunk_has_repair = _rewrite_chunk(
                chunk_id=chunk_id,
                chunk_data=chunk_data,
                expected_date=expected_date,
                expected_time=expected_time,
            )
            chunks.append((chunk_id, rewritten_chunk))
            has_repair = has_repair or chunk_has_repair

    if not has_repair or dry_run:
        return has_repair

    with tempfile.NamedTemporaryFile(delete=False, dir=path.parent, suffix=".tmp") as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(b"RIFF")
        temp_file.write(b"\x00" * 4)
        temp_file.write(b"WAVE")
        wave_payload_size = 4
        for chunk_id, chunk_data in chunks:
            temp_file.write(chunk_id)
            temp_file.write(struct.pack("<I", len(chunk_data)))
            temp_file.write(chunk_data)
            wave_payload_size += 8 + len(chunk_data)
            if len(chunk_data) % 2 == 1:
                temp_file.write(b"\x00")
                wave_payload_size += 1
        temp_file.seek(4)
        temp_file.write(struct.pack("<I", wave_payload_size))
    os.replace(temp_path, path)
    return True


def _rewrite_chunk(
    *, chunk_id: bytes, chunk_data: bytes, expected_date: str, expected_time: str
) -> tuple[bytes, bool]:
    if chunk_id == b"bext":
        return _rewrite_bext_chunk(chunk_data, expected_date=expected_date, expected_time=expected_time)
    if chunk_id == b"iXML":
        return _rewrite_ixml_chunk(chunk_data, expected_date=expected_date)
    return chunk_data, False


def _rewrite_bext_chunk(chunk_data: bytes, *, expected_date: str, expected_time: str) -> tuple[bytes, bool]:
    if len(chunk_data) < 338:
        return chunk_data, False

    expected_date_bytes = expected_date.encode("ascii")
    expected_time_bytes = expected_time.encode("ascii")
    current_date_bytes = chunk_data[320:330]
    current_time_bytes = chunk_data[330:338]
    if current_date_bytes == expected_date_bytes and current_time_bytes == expected_time_bytes:
        return chunk_data, False

    rewritten = bytearray(chunk_data)
    rewritten[320:330] = expected_date_bytes
    rewritten[330:338] = expected_time_bytes
    return bytes(rewritten), True


def _rewrite_ixml_chunk(chunk_data: bytes, *, expected_date: str) -> tuple[bytes, bool]:
    trimmed_chunk = chunk_data.rstrip(b"\x00")
    if not trimmed_chunk:
        return chunk_data, False
    try:
        xml = trimmed_chunk.decode("utf-8")
    except UnicodeDecodeError:
        return chunk_data, False

    rewritten_xml, replaced = _replace_xml_tag(xml, "BWF_ORIGINATION_DATE", expected_date)
    if not replaced:
        return chunk_data, False

    rewritten_chunk = rewritten_xml.encode("utf-8") + chunk_data[len(trimmed_chunk) :]
    return rewritten_chunk, rewritten_chunk != chunk_data


def _replace_xml_tag(xml: str, tag: str, value: str) -> tuple[str, bool]:
    pattern = re.compile(rf"<{tag}>(.*?)</{tag}>", re.IGNORECASE)
    replacements = {"updated": False}

    def _replace(match: re.Match[str]) -> str:
        replacements["updated"] = True
        return f"<{tag}>{value}</{tag}>"

    rewritten = pattern.sub(_replace, xml)
    return rewritten, replacements["updated"]
