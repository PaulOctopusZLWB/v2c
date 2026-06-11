from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.tasks import enqueue_task_in_conn


@dataclass(frozen=True)
class IngestScanResult:
    files: list[Path]


@dataclass(frozen=True)
class IngestImportResult:
    imported_files: int


def scan_audio_files(*, source_dir: Path) -> IngestScanResult:
    return IngestScanResult(files=sorted(source_dir.glob("*.wav")))


def import_audio_files(*, config: AppConfig, source_dir: Path) -> IngestImportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = import_audio_files_in_conn(conn, config=config, source_dir=source_dir)
        conn.commit()
        return IngestImportResult(imported_files=imported)
    finally:
        conn.close()


def import_audio_files_in_conn(conn: sqlite3.Connection, *, config: AppConfig, source_dir: Path) -> int:
    imported = 0
    for source_path in scan_audio_files(source_dir=source_dir).files:
        sha256 = _sha256(source_path)
        existing = conn.execute(
            "select 1 from audio_files where source_path = ? and sha256 = ?",
            (str(source_path), sha256),
        ).fetchone()
        if existing:
            continue
        recorded_date = _recorded_date_from_name(source_path)
        local_dir = config.raw_audio_dir / recorded_date
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / source_path.name
        shutil.copy2(source_path, local_path)
        audio_file_id = f"aud_{uuid4().hex}"
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audio_file_id,
                config.source_device,
                str(source_path),
                str(local_path),
                sha256,
                _duration_ms(source_path),
                f"{recorded_date}T00:00:00+08:00",
                datetime.now(timezone.utc).isoformat(),
                "imported",
            ),
        )
        enqueue_task_in_conn(conn, task_type="vad", target_type="audio_file", target_id=audio_file_id)
        imported += 1
    return imported


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        return round(wav.getnframes() / wav.getframerate() * 1000)


def _recorded_date_from_name(path: Path) -> str:
    match = re.search(r"_(\d{8})_", path.name)
    if not match:
        return datetime.now().date().isoformat()
    raw = match.group(1)
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
