from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from personal_context_node.core.ports.archive import ArchiveResult


class LocalFilesystemArchiveAdapter:
    def __init__(self, *, root: Path, require_existing_root: bool = False) -> None:
        self.root = root
        self.require_existing_root = require_existing_root

    def archive_file(self, *, source_path: Path, relative_path: Path, expected_sha256: str) -> ArchiveResult:
        if self.require_existing_root and not self.root.exists():
            return ArchiveResult(archive_path=self.root / relative_path, verified=False, reason="archive root unavailable")
        target_path = self.root / relative_path
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            return self.verify_file(archive_path=target_path, expected_sha256=expected_sha256)
        except OSError as exc:
            return ArchiveResult(archive_path=target_path, verified=False, reason=str(exc))

    def verify_file(self, *, archive_path: Path, expected_sha256: str) -> ArchiveResult:
        if not archive_path.exists():
            return ArchiveResult(archive_path=archive_path, verified=False, reason="archive file missing")
        actual_sha256 = _sha256(archive_path)
        if actual_sha256 != expected_sha256:
            return ArchiveResult(archive_path=archive_path, verified=False, reason="hash mismatch")
        return ArchiveResult(archive_path=archive_path, verified=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
