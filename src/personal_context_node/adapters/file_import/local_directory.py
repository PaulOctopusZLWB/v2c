from __future__ import annotations

import fnmatch
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from personal_context_node.core.ports.errors import RetryablePortError
from personal_context_node.core.ports.file_import import (
    ImportedRawAudio,
    MountedDevice,
    SourceAudioFile,
    StableSourceAudioFile,
)
from personal_context_node.ingest import (
    _duration_ms,
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
        volume_root: Path | None = None,
    ) -> None:
        self.device_roots = device_roots
        self.device_label = device_label
        self.audio_globs = tuple(audio_globs or ("*.wav", "*.WAV"))
        self.volume_name_patterns = tuple(volume_name_patterns or ())
        self.volume_root = volume_root

    def discover_devices(self) -> list[MountedDevice]:
        return [
            MountedDevice(device_id=str(root), label=self.device_label, root_path=root)
            for root in self._candidate_device_roots()
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
        for path in _iter_visible_files(root_path):
            if any(_matches_audio_glob(path, root_path, pattern) for pattern in self.audio_globs):
                paths.add(path)
        return sorted(paths)

    def _candidate_device_roots(self) -> list[Path]:
        if self.device_roots:
            return self.device_roots
        if self.volume_root is None or not self.volume_root.exists():
            return []
        return sorted(path for path in self.volume_root.iterdir() if path.is_dir() and not path.is_symlink())

    def _matches_volume_name(self, root: Path) -> bool:
        if not self.volume_name_patterns:
            return True
        return any(fnmatch.fnmatch(root.name, pattern) for pattern in self.volume_name_patterns)

    def wait_until_stable(self, source: SourceAudioFile, *, stable_seconds: int) -> StableSourceAudioFile:
        if not is_file_stable(source.source_path, settle_seconds=stable_seconds):
            # A file still being written by the device is inherently retryable (§28).
            raise RetryablePortError(f"source file is not stable: {source.source_path}")
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


def _has_hidden_part(path: Path, root_path: Path) -> bool:
    try:
        relative = path.relative_to(root_path)
    except ValueError:
        relative = path
    return any(part.startswith(".") for part in relative.parts)


def _iter_visible_files(root_path: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root_path):
        dir_names[:] = [name for name in dir_names if not name.startswith(".")]
        for file_name in file_names:
            path = Path(current_root) / file_name
            if not file_name.startswith(".") and not _has_hidden_part(path, root_path):
                files.append(path)
    return files


def _matches_audio_glob(path: Path, root_path: Path, pattern: str) -> bool:
    relative = path.relative_to(root_path)
    if "/" not in pattern and not pattern.startswith("**"):
        return len(relative.parts) == 1 and fnmatch.fnmatch(path.name, pattern)
    relative_posix = PurePosixPath(relative.as_posix())
    if pattern.startswith("**/") and relative_posix.match(pattern.removeprefix("**/")):
        return True
    return relative_posix.match(pattern)
