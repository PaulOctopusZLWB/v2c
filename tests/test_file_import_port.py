from __future__ import annotations

import inspect
from pathlib import Path

from personal_context_node.core.ports.file_import import (
    FileImportPort,
    ImportedRawAudio,
    MountedDevice,
    SourceAudioFile,
    StableSourceAudioFile,
)


def test_file_import_port_contract_matches_design() -> None:
    methods = {
        name: inspect.signature(getattr(FileImportPort, name))
        for name in ["discover_devices", "discover_audio_files", "wait_until_stable", "copy_to_raw_store"]
    }

    assert list(methods["discover_devices"].parameters) == ["self"]
    assert list(methods["discover_audio_files"].parameters) == ["self", "device"]
    assert list(methods["wait_until_stable"].parameters) == ["self", "source", "stable_seconds"]
    assert methods["wait_until_stable"].parameters["stable_seconds"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(methods["copy_to_raw_store"].parameters) == ["self", "source", "destination_dir"]


def test_file_import_port_boundary_objects_are_value_types(tmp_path: Path) -> None:
    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "device")
    source = SourceAudioFile(
        device=device,
        source_path=tmp_path / "device" / "audio.wav",
        size_bytes=1024,
        mtime_ns=123456789,
    )
    stable = StableSourceAudioFile(source=source, stable_checked_at="2087-05-10T00:00:00Z")
    imported = ImportedRawAudio(
        source=stable,
        local_raw_path=tmp_path / "raw" / "audio.wav",
        sha256="sha256:test",
        duration_ms=1000,
        recorded_at="2087-05-10T08:00:00+08:00",
    )

    assert source.device == device
    assert stable.source == source
    assert imported.source == stable
    assert imported.local_raw_path.name == "audio.wav"
