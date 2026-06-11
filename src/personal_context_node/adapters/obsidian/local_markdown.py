from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from personal_context_node.core.ports.obsidian import MarkdownNote, PublishResult, ReviewBlock


class LocalMarkdownObsidianAdapter:
    def __init__(self, *, vault_root: Path) -> None:
        self.vault_root = vault_root

    def publish_note(self, note: MarkdownNote) -> PublishResult:
        note.path.parent.mkdir(parents=True, exist_ok=True)
        note.path.write_text(note.text, encoding="utf-8")
        return PublishResult(note_path=note.path)

    def read_review_blocks(self, note_path: Path) -> list[ReviewBlock]:
        text = note_path.read_text(encoding="utf-8")
        return [
            ReviewBlock(
                note_path=note_path,
                block_type=match.group("block_type"),
                target_id=match.group("target_id"),
                version=match.group("version"),
                body=_strip_fenced_yaml(match.group("body")).strip(),
            )
            for match in _REVIEW_BLOCK_RE.finditer(text)
        ]

    def list_changed_review_notes(self, since: datetime) -> list[Path]:
        cutoff = since.timestamp()
        notes: list[Path] = []
        for directory in [
            self.vault_root / "30_Memory_Candidates",
            self.vault_root / "90_System" / "Speaker_Review",
        ]:
            if not directory.exists():
                continue
            for path in directory.glob("*.md"):
                if path.stat().st_mtime > cutoff:
                    notes.append(path)
        return sorted(notes)


_REVIEW_BLOCK_RE = re.compile(
    r'<!--\s*pcn:review start\b[^>]*type="(?P<block_type>[^"]+)"[^>]*'
    r'candidate_id="(?P<target_id>[^"]+)"[^>]*version="(?P<version>[^"]+)"[^>]*-->'
    r"(?P<body>.*?)"
    r'<!--\s*pcn:review end\b[^>]*candidate_id="(?P=target_id)"[^>]*-->',
    flags=re.DOTALL,
)


def _strip_fenced_yaml(body: str) -> str:
    match = re.search(r"```yaml\n(?P<yaml>.*?)\n```", body, flags=re.DOTALL)
    return match.group("yaml") if match else body
