from __future__ import annotations

import shlex
from pathlib import Path

from personal_context_node.adapters.archive.command import CommandArchiveAdapter
from personal_context_node.adapters.archive.local_filesystem import LocalFilesystemArchiveAdapter
from personal_context_node.config import AppConfig


def build_archive_adapter(
    *,
    config: AppConfig,
    archive_root: Path | None = None,
    archive_backend: str | None = None,
    archive_command: str | None = None,
    require_existing_root: bool = False,
):
    resolved_root = archive_root or config.nas_archive_root
    resolved_backend = archive_backend or config.archive_backend
    resolved_command = archive_command or config.archive_command
    if resolved_backend == "filesystem":
        return LocalFilesystemArchiveAdapter(root=resolved_root, require_existing_root=require_existing_root)
    if resolved_backend == "command":
        if not resolved_command:
            raise ValueError("archive command is required when archive backend is command")
        return CommandArchiveAdapter(
            root=resolved_root,
            command=shlex.split(resolved_command),
            timeout_seconds=config.command_timeout_seconds,
        )
    raise ValueError("archive backend must be 'filesystem' or 'command'")
