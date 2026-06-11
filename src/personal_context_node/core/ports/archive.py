from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ArchiveResult:
    archive_path: Path
    verified: bool
    reason: str | None = None


class ArchivePort(Protocol):
    def archive_file(self, *, source_path: Path, relative_path: Path, expected_sha256: str) -> ArchiveResult:
        """Copy a durable artifact to archive storage and verify its hash."""
