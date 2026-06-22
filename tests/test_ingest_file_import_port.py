from __future__ import annotations

import wave
from datetime import datetime, timezone
from pathlib import Path

import personal_context_node.ingest as ingest_module
from personal_context_node.adapters.file_import.local_directory import LocalDirectoryFileImportAdapter
from personal_context_node.config import AppConfig, DeviceDiscoveryConfig
from personal_context_node.core.ports.file_import import ImportedRawAudio, MountedDevice, SourceAudioFile, StableSourceAudioFile
from personal_context_node.ingest import import_audio_files_from_port
from personal_context_node.storage.sqlite import connect, fetch_all


def test_import_unlinks_orphan_copy_on_post_copy_dedup(tmp_path: Path, monkeypatch) -> None:
    device_root = tmp_path / "DJI_MIC"
    source = device_root / "TX02_MIC001_20250610_173550_orig.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
    importer = LocalDirectoryFileImportAdapter(device_roots=[device_root], device_label="DJI Mic 3")
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device_root, stable_seconds=0),
    )
    assert import_audio_files_from_port(config=config, importer=importer).imported_files == 1

    # Simulate a concurrent race: the pre-copy source guard misses, so the second pass copies
    # again and only the post-copy sha256 dedup catches the duplicate.
    monkeypatch.setattr(ingest_module, "_source_audio_exists", lambda conn, source: False)
    assert import_audio_files_from_port(config=config, importer=importer).imported_files == 0

    raw_files = sorted(config.raw_audio_dir.rglob("*.wav"))
    assert len(raw_files) == 1  # the orphan copy written by the losing pass was unlinked


def test_import_audio_files_from_port_registers_stable_sources_and_enqueues_vad(tmp_path: Path) -> None:
    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "mounted_dji")
    source = SourceAudioFile(
        device=device,
        source_path=device.root_path / "TX02_MIC001_20250610_173550_orig.wav",
        size_bytes=1024,
        mtime_ns=123456789,
    )
    importer = RecordingFileImporter(device=device, source=source)
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device.root_path, stable_seconds=7),
    )

    result = import_audio_files_from_port(config=config, importer=importer)

    assert result.imported_files == 1
    assert importer.calls == [
        "discover_devices",
        "discover_audio_files:dev_dji",
        "wait_until_stable:7",
        "copy_to_raw_store",
    ]
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(
            conn,
            "select source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, status from audio_files",
        )
        tasks = fetch_all(conn, "select task_type, target_type, status from tasks")
    finally:
        conn.close()
    assert audio_files == [
        {
            "source_device": "DJI Mic 3",
            "source_path": str(source.source_path),
            "source_size_bytes": 1024,
            "source_mtime_ns": 123456789,
            "local_raw_path": str(config.raw_audio_dir / "2025-06-10" / source.source_path.name),
            "sha256": "sha256:ported",
            "duration_ms": 1000,
            "recorded_at": "2025-06-10T17:35:50+08:00",
            "status": "imported",
        }
    ]
    assert tasks == [{"task_type": "vad", "target_type": "audio_file", "status": "pending"}]


def test_import_enqueues_vad_task_with_recorded_date_priority(tmp_path: Path) -> None:
    # The backlog is ordered by recorded date via tasks.priority (incremental-day-review Task 2):
    # the import must stamp each vad task with a date-derived priority so earlier days drain first.
    # A regression that drops `priority=` reverts to the flat default 100 and defeats date-major
    # drain — this test pins the wiring. recorded_at 2025-06-10 -> (2025-06-10 - 2000-01-01).days.
    from datetime import date

    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "mounted_dji")
    source = SourceAudioFile(
        device=device,
        source_path=device.root_path / "TX02_MIC001_20250610_173550_orig.wav",
        size_bytes=1024,
        mtime_ns=123456789,
    )
    importer = RecordingFileImporter(device=device, source=source)  # copies with recorded_at 2025-06-10
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device.root_path, stable_seconds=7),
    )

    assert import_audio_files_from_port(config=config, importer=importer).imported_files == 1

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select priority from tasks where task_type = 'vad'")
    finally:
        conn.close()
    expected = (date(2025, 6, 10) - date(2000, 1, 1)).days
    assert expected == 9292  # derivation pinned: a flat default of 100 would not equal this
    assert [r["priority"] for r in rows] == [expected]


def test_import_audio_files_from_port_skips_existing_source_snapshot_before_copy(tmp_path: Path) -> None:
    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "mounted_dji")
    source = SourceAudioFile(
        device=device,
        source_path=device.root_path / "TX02_MIC001_20250610_173550_orig.wav",
        size_bytes=1024,
        mtime_ns=123456789,
    )
    importer = RecordingFileImporter(device=device, source=source)
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device.root_path, stable_seconds=7),
    )
    assert import_audio_files_from_port(config=config, importer=importer).imported_files == 1
    importer.calls.clear()

    result = import_audio_files_from_port(config=config, importer=importer)

    assert result.imported_files == 0
    assert importer.calls == [
        "discover_devices",
        "discover_audio_files:dev_dji",
    ]


class RecordingFileImporter:
    def __init__(self, *, device: MountedDevice, source: SourceAudioFile) -> None:
        self.device = device
        self.source = source
        self.calls: list[str] = []

    def discover_devices(self) -> list[MountedDevice]:
        self.calls.append("discover_devices")
        return [self.device]

    def discover_audio_files(self, device: MountedDevice) -> list[SourceAudioFile]:
        self.calls.append(f"discover_audio_files:{device.device_id}")
        return [self.source]

    def wait_until_stable(self, source: SourceAudioFile, *, stable_seconds: int) -> StableSourceAudioFile:
        self.calls.append(f"wait_until_stable:{stable_seconds}")
        return StableSourceAudioFile(source=source, stable_checked_at=datetime.now(timezone.utc).isoformat())

    def copy_to_raw_store(self, source: StableSourceAudioFile, destination_dir: Path) -> ImportedRawAudio:
        self.calls.append("copy_to_raw_store")
        local_raw_path = destination_dir / "2025-06-10" / source.source.source_path.name
        local_raw_path.parent.mkdir(parents=True, exist_ok=True)
        local_raw_path.write_bytes(b"raw")
        return ImportedRawAudio(
            source=source,
            local_raw_path=local_raw_path,
            sha256="sha256:ported",
            duration_ms=1000,
            recorded_at="2025-06-10T17:35:50+08:00",
        )
