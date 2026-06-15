from __future__ import annotations

import wave
from pathlib import Path

import pytest

from personal_context_node.adapters.file_import import local_directory as local_directory_module
from personal_context_node.adapters.file_import.local_directory import (
    LocalDirectoryFileImportAdapter,
    _reserve_destination_path,
)
from personal_context_node.core.ports.file_import import MountedDevice, SourceAudioFile, StableSourceAudioFile


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


def test_local_directory_file_import_adapter_discovers_devices_from_volume_root(tmp_path: Path) -> None:
    volume_root = tmp_path / "Volumes"
    mic_root = volume_root / "NO NAME"
    other_root = volume_root / "USB_DRIVE"
    mic_root.mkdir(parents=True)
    other_root.mkdir()
    adapter = LocalDirectoryFileImportAdapter(
        device_roots=[],
        device_label="DJI Mic 3",
        volume_name_patterns=["NO NAME"],
        volume_root=volume_root,
    )

    devices = adapter.discover_devices()

    assert [device.root_path for device in devices] == [mic_root]


def test_local_directory_file_import_adapter_skips_hidden_system_audio_directories(tmp_path: Path) -> None:
    device_root = tmp_path / "NO NAME"
    real_audio = device_root / "TX_MIC001" / "TX02_MIC013_20870511_190910_orig.wav"
    trash_audio = device_root / ".Trashes" / "501" / "TX02_MIC014_20870511_191010_orig.wav"
    spotlight_audio = device_root / ".Spotlight-V100" / "TX02_MIC015_20870511_192010_orig.wav"
    _write_tiny_wav(real_audio)
    _write_tiny_wav(trash_audio)
    _write_tiny_wav(spotlight_audio)
    adapter = LocalDirectoryFileImportAdapter(
        device_roots=[device_root],
        device_label="DJI Mic 3",
        audio_globs=["**/*.wav"],
        volume_name_patterns=["NO NAME"],
    )

    sources = adapter.discover_audio_files(adapter.discover_devices()[0])

    assert [source.source_path for source in sources] == [real_audio]


def test_copy_to_raw_store_keeps_existing_file_when_name_collides(tmp_path: Path) -> None:
    device = MountedDevice(device_id="dev", label="DJI Mic 3", root_path=tmp_path / "device")
    first_source = _stable_source(device, tmp_path / "first" / "TX01_MIC001_20870510_120000_orig.wav")
    second_source = _stable_source(device, tmp_path / "second" / "TX01_MIC001_20870510_120000_orig.wav")
    adapter = LocalDirectoryFileImportAdapter(device_roots=[], device_label="DJI Mic 3")
    destination = tmp_path / "data" / "audio" / "raw"

    first = adapter.copy_to_raw_store(first_source, destination)
    second = adapter.copy_to_raw_store(second_source, destination)

    assert first.local_raw_path != second.local_raw_path
    assert first.local_raw_path.exists()
    assert second.local_raw_path.exists()


def test_reserve_destination_path_reserves_atomically(tmp_path: Path) -> None:
    # Each reservation creates the file, so a second reservation of the same name cannot
    # pick the same path — closing the check-then-copy race between concurrent imports.
    target = tmp_path / "raw"
    target.mkdir()

    first = _reserve_destination_path(target, "TX01_MIC001_20870510_120000_orig.wav")
    second = _reserve_destination_path(target, "TX01_MIC001_20870510_120000_orig.wav")

    assert first != second
    assert first.exists()
    assert second.exists()


def test_copy_to_raw_store_cleans_up_reservation_when_copy_fails(tmp_path: Path, monkeypatch) -> None:
    device = MountedDevice(device_id="dev", label="DJI Mic 3", root_path=tmp_path / "device")
    source = _stable_source(device, tmp_path / "src" / "TX01_MIC001_20870510_120000_orig.wav")
    adapter = LocalDirectoryFileImportAdapter(device_roots=[], device_label="DJI Mic 3")
    destination = tmp_path / "data" / "audio" / "raw"

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(local_directory_module.shutil, "copy2", _boom)

    with pytest.raises(OSError):
        adapter.copy_to_raw_store(source, destination)

    assert list(destination.rglob("*.wav")) == []  # reserved placeholder cleaned up


def _stable_source(device: MountedDevice, path: Path) -> StableSourceAudioFile:
    _write_tiny_wav(path)
    stat = path.stat()
    return StableSourceAudioFile(
        source=SourceAudioFile(
            device=device,
            source_path=path,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        ),
        stable_checked_at="2087-05-10T12:00:00+08:00",
    )


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
