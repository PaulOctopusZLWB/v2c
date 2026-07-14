from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.config import AppConfig, DeviceDiscoveryConfig
from personal_context_node.device_discovery import discover_import_sources
from personal_context_node.ingest import import_audio_files


def _wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16)


def test_detects_mounted_device_volume_and_counts_new_files(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    device_root = volumes / "NO NAME"
    _wav(device_root / "TX01_MIC001_20250610_090000_orig.wav")
    _wav(device_root / "TX01_MIC002_20250610_091000_orig.wav")
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(volume_root=volumes, volume_name_patterns=("NO NAME", "DJI*")),
    )

    sources = discover_import_sources(config=config)

    device = next(s for s in sources if s["kind"] == "device")
    assert device["root_path"] == str(device_root)
    assert device["audio_count"] == 2
    assert device["label"]  # device label present


def test_device_audio_count_excludes_imported_source_snapshots(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    device_root = volumes / "NO NAME"
    old_file = device_root / "TX01_MIC001_20250610_090000_orig.wav"
    new_file = device_root / "TX01_MIC002_20250610_091000_orig.wav"
    _wav(old_file)
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(volume_root=volumes, volume_name_patterns=("NO NAME",)),
    )
    assert import_audio_files(config=config, source_dir=device_root).imported_files == 1
    _wav(new_file)

    sources = discover_import_sources(config=config)

    device = next(s for s in sources if s["kind"] == "device")
    assert device["audio_count"] == 1


def test_no_device_returns_known_sources_only(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"  # empty
    volumes.mkdir()
    known = tmp_path / "library"
    _wav(known / "a.wav")
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(volume_root=volumes, volume_name_patterns=("NO NAME",), root_path=known),
    )

    sources = discover_import_sources(config=config)

    assert all(s["kind"] != "device" for s in sources)
    known_paths = [s["root_path"] for s in sources if s["kind"] == "known"]
    assert str(known) in known_paths
