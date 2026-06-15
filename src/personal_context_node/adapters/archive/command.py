from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from personal_context_node.adapters.command_runner import run_command
from personal_context_node.core.ports.archive import ArchiveResult


class CommandArchiveAdapter:
    """Archive adapter for rsync-like commands.

    The command is invoked with placeholders expanded. Supported placeholders:
    `{source_path}`, `{archive_path}`, and `{relative_path}`. When no placeholders
    are present, source and archive paths are appended.
    """

    def __init__(self, *, root: Path, command: list[str], timeout_seconds: float = 3600.0) -> None:
        if not command:
            raise ValueError("archive command must not be empty")
        self.root = root
        self.command = command
        self.timeout_seconds = timeout_seconds

    def archive_file(self, *, source_path: Path, relative_path: Path, expected_sha256: str) -> ArchiveResult:
        archive_path = self.root / relative_path
        # A missing root means the NAS is unavailable: report pending instead of
        # fabricating the archive tree locally and "verifying" against it, which would
        # mark unarchived raw as archived and let cleanup delete the only copy
        # (§13.1 must not block; §13.2 never auto-delete unarchived raw).
        if not self.root.exists():
            return ArchiveResult(archive_path=archive_path, verified=False, reason="archive root unavailable")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._archive_command(source_path=source_path, archive_path=archive_path, relative_path=relative_path)
        try:
            completed = run_command(command, timeout_seconds=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return ArchiveResult(
                archive_path=archive_path,
                verified=False,
                reason=f"archive command timed out after {self.timeout_seconds:g}s",
            )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            reason = f"archive command failed with exit {completed.returncode}"
            if stderr:
                reason = f"{reason}: {stderr}"
            return ArchiveResult(archive_path=archive_path, verified=False, reason=reason)
        return self.verify_file(archive_path=archive_path, expected_sha256=expected_sha256)

    def verify_file(self, *, archive_path: Path, expected_sha256: str) -> ArchiveResult:
        if not archive_path.exists():
            return ArchiveResult(archive_path=archive_path, verified=False, reason="archive file missing")
        actual_sha256 = _sha256(archive_path)
        if actual_sha256 != expected_sha256:
            return ArchiveResult(archive_path=archive_path, verified=False, reason="hash mismatch")
        return ArchiveResult(archive_path=archive_path, verified=True)

    def _archive_command(self, *, source_path: Path, archive_path: Path, relative_path: Path) -> list[str]:
        replacements = {
            "{source_path}": str(source_path),
            "{archive_path}": str(archive_path),
            "{relative_path}": str(relative_path),
        }
        if any(any(token in part for token in replacements) for part in self.command):
            return [_replace_placeholders(part, replacements) for part in self.command]
        return [*self.command, str(source_path), str(archive_path)]


def _replace_placeholders(value: str, replacements: dict[str, str]) -> str:
    for token, replacement in replacements.items():
        value = value.replace(token, replacement)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
