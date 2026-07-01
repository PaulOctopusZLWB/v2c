from __future__ import annotations

import shutil
from pathlib import Path

from personal_context_node.adapters.command_runner import run_command


def normalize_to_wav(*, source_path: Path, target_path: Path, timeout_seconds: float = 3600.0) -> Path:
    suffix = source_path.suffix.lower()
    if suffix in {".wav", ".wave"}:
        if target_path.suffix.lower() != ".wav":
            raise ValueError("target_path must use .wav for normalized audio")
        if source_path != target_path:
            target_path.write_bytes(source_path.read_bytes())
        return target_path

    target_path = target_path.with_suffix(".wav")
    ffmpeg = _ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError(
            f"ffmpeg is required to ingest {source_path.name}; install ffmpeg/imageio-ffmpeg or pre-convert to WAV first"
        )

    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(target_path),
    ]
    result = run_command(command, timeout_seconds=timeout_seconds)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {source_path}: {result.stderr.strip()}")
    if not target_path.exists() or target_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no wav output for {source_path}")

    return target_path


def _ffmpeg_executable() -> str | None:
    return shutil.which("ffmpeg") or _imageio_ffmpeg_executable()


def _imageio_ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    return str(imageio_ffmpeg.get_ffmpeg_exe())
