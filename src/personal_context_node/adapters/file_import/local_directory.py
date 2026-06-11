from __future__ import annotations

import fnmatch
import shutil
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.core.ports.file_import import (
    ImportedRawAudio,
    MountedDevice,
    SourceAudioFile,
    StableSourceAudioFile,
)
from personal_context_node.ingest import (
    _duration_ms,
    _iter_audio_paths,
    _recorded_at_from_name,
    _repair_wav_file_metadata,
    _sha256,
    is_file_stable,
)


class LocalDirectoryFileImportAdapter:
    def __init__(
        self,
        *,
        device_roots: list[Path],
        device_label: str,
        audio_globs: list[str] | tuple[str, ...] | None = None,
        volume_name_patterns: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.device_roots = device_roots
        self.device_label = device_label
        self.audio_globs = tuple(audio_globs or ("*.wav", "*.WAV"))
        self.volume_name_patterns = tuple(volume_name_patterns or ())

    def discover_devices(self) -> list[MountedDevice]:
        return [
            MountedDevice(device_id=str(root), label=self.device_label, root_path=root)
            for root in self.device_roots
            if root.exists() and root.is_dir() and self._matches_volume_name(root)
        ]

    def discover_audio_files(self, device: MountedDevice) -> list[SourceAudioFile]:
        sources: list[SourceAudioFile] = []
        for path in self._iter_configured_audio_paths(device.root_path):
            stat = path.stat()
            sources.append(
                SourceAudioFile(
                    device=device,
                    source_path=path,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            )
        return sources

    def _iter_configured_audio_paths(self, root_path: Path) -> list[Path]:
        paths: set[Path] = set()
        for pattern in self.audio_globs:
            paths.update(path for path in root_path.glob(pattern) if path.is_file())
        return sorted(paths)

    def _matches_volume_name(self, root: Path) -> bool:
        if not self.volume_name_patterns:
            return True
        return any(fnmatch.fnmatch(root.name, pattern) for pattern in self.volume_name_patterns)

    def wait_until_stable(self, source: SourceAudioFile, *, stable_seconds: int) -> StableSourceAudioFile:
        if not is_file_stable(source.source_path, settle_seconds=stable_seconds):
            raise RuntimeError(f"source file is not stable: {source.source_path}")
        return StableSourceAudioFile(
            source=source,
            stable_checked_at=datetime.now(timezone.utc).isoformat(),
        )

    def copy_to_raw_store(self, source: StableSourceAudioFile, destination_dir: Path) -> ImportedRawAudio:
        recorded_at = _recorded_at_from_name(source.source.source_path)
        target_dir = destination_dir / recorded_at[:10]
        target_dir.mkdir(parents=True, exist_ok=True)
        local_raw_path = target_dir / source.source.source_path.name
        shutil.copy2(source.source.source_path, local_raw_path)
        _repair_wav_file_metadata(local_raw_path, recorded_at)
        return ImportedRawAudio(
            source=source,
            local_raw_path=local_raw_path,
            sha256=_sha256(local_raw_path),
            duration_ms=_duration_ms(local_raw_path),
            recorded_at=recorded_at,
        )
