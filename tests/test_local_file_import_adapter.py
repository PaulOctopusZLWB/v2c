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


def test_local_directory_file_import_adapter_discovers_configured_audio_globs_recursively(tmp_path: Path) -> None:
    device_root = tmp_path / "DJI_MIC"
    nested_audio = device_root / "REC" / "TX02_MIC013_20870511_190910_orig.wav"
    upper_audio = device_root / "REC" / "TX02_MIC014_20870511_191010_orig.WAV"
    ignored_text = device_root / "REC" / "notes.txt"
    _write_tiny_wav(nested_audio)
    _write_tiny_wav(upper_audio)
    ignored_text.write_text("not audio", encoding="utf-8")
    adapter = LocalDirectoryFileImportAdapter(
        device_roots=[device_root],
        device_label="DJI Mic 3",
        audio_globs=["**/*.WAV", "**/*.wav"],
    )

    sources = adapter.discover_audio_files(adapter.discover_devices()[0])

    assert [source.source_path for source in sources] == [nested_audio, upper_audio]


def test_local_directory_file_import_adapter_filters_devices_by_volume_name_patterns(tmp_path: Path) -> None:
    dji_root = tmp_path / "DJI_MIC"
    other_root = tmp_path / "USB_DRIVE"
    dji_root.mkdir()
    other_root.mkdir()
    adapter = LocalDirectoryFileImportAdapter(
        device_roots=[dji_root, other_root],
        device_label="DJI Mic 3",
        volume_name_patterns=["DJI*", "MIC*"],
    )

    devices = adapter.discover_devices()

    assert [device.root_path for device in devices] == [dji_root]


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
