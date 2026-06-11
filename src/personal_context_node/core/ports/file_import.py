from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class MountedDevice:
    device_id: str
    label: str
    root_path: Path


@dataclass(frozen=True)
class SourceAudioFile:
    device: MountedDevice
    source_path: Path
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class StableSourceAudioFile:
    source: SourceAudioFile
    stable_checked_at: str


@dataclass(frozen=True)
class ImportedRawAudio:
    source: StableSourceAudioFile
    local_raw_path: Path
    sha256: str
    duration_ms: int
    recorded_at: str


class FileImportPort(Protocol):
    def discover_devices(self) -> list[MountedDevice]:
        """Return mounted recording devices available for import."""

    def discover_audio_files(self, device: MountedDevice) -> list[SourceAudioFile]:
        """Return source audio files discovered on a mounted device."""

    def wait_until_stable(self, source: SourceAudioFile, *, stable_seconds: int) -> StableSourceAudioFile:
        """Block until a source file is stable enough to copy."""

    def copy_to_raw_store(self, source: StableSourceAudioFile, destination_dir: Path) -> ImportedRawAudio:
        """Copy source audio into the local raw store and return measured metadata."""
