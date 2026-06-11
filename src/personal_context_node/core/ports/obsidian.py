from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class MarkdownNote:
    path: Path
    text: str


@dataclass(frozen=True)
class PublishResult:
    note_path: Path


@dataclass(frozen=True)
class ReviewBlock:
    note_path: Path
    block_type: str
    target_id: str
    version: str
    body: str


class ObsidianPort(Protocol):
    def publish_note(self, note: MarkdownNote) -> PublishResult:
        """Write a Markdown note into the Obsidian vault."""

    def read_review_blocks(self, note_path: Path) -> list[ReviewBlock]:
        """Read pcn:review blocks from a Markdown note."""

    def list_changed_review_notes(self, since: datetime) -> list[Path]:
        """List review notes changed after the given timestamp."""
