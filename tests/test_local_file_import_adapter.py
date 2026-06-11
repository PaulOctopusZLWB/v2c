from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.adapters.file_import.local_directory import LocalDirectoryFileImportAdapter


def test_local_directory_file_import_adapter_copies_stable_audio_to_raw_store(tmp_path: Path) -> None:
    device_root = tmp_path / "DJI_MIC"
    source_audio = device_root / "TX02_MIC013_20870511_190910_orig.wav"
    _write_tiny_wav(source_audio)
    adapter = LocalDirectoryFileImportAdapter(device_roots=[device_root], device_label="DJI Mic 3")

    devices = adapter.discover_devices()
    sources = adapter.discover_audio_files(devices[0])
    stable = adapter.wait_until_stable(sources[0], stable_seconds=0)
    imported = adapter.copy_to_raw_store(stable, tmp_path / "raw")

    assert devices[0].device_id == str(device_root)
    assert devices[0].label == "DJI Mic 3"
    assert sources[0].source_path == source_audio
    assert imported.local_raw_path == tmp_path / "raw" / "2025-06-11" / source_audio.name
    assert imported.local_raw_path.exists()
    assert imported.sha256.startswith("sha256:")
    assert imported.duration_ms == 1000
    assert imported.recorded_at == "2025-06-11T19:09:10+08:00"


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
