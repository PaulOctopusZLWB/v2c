from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig, DeviceDiscoveryConfig
from personal_context_node.core.ports.file_import import ImportedRawAudio, MountedDevice, SourceAudioFile, StableSourceAudioFile
from personal_context_node.ingest import import_audio_files_from_port
from personal_context_node.storage.sqlite import connect, fetch_all


def test_import_audio_files_from_port_registers_stable_sources_and_enqueues_vad(tmp_path: Path) -> None:
    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "mounted_dji")
    source = SourceAudioFile(
        device=device,
        source_path=device.root_path / "TX02_MIC001_20870510_173550_orig.wav",
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
