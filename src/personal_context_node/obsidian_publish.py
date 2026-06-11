from __future__ import annotations

from dataclasses import dataclass

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_daily import publish_daily_note
from personal_context_node.obsidian_review import publish_candidate_review
from personal_context_node.obsidian_sessions import publish_session_notes
from personal_context_node.speaker_review import publish_speaker_review


@dataclass(frozen=True)
class ObsidianPublishResult:
    daily_notes_written: int
    session_notes_written: int
    candidate_review_written: int
    speaker_review_written: int


def publish_obsidian_day(*, config: AppConfig, day: str) -> ObsidianPublishResult:
    daily_result = publish_daily_note(config=config, day=day)
    session_result = publish_session_notes(config=config, day=day)
    publish_candidate_review(config=config, day=day)
    publish_speaker_review(config=config, day=day)
    return ObsidianPublishResult(
        daily_notes_written=daily_result.notes_written,
        session_notes_written=session_result.notes_written,
        candidate_review_written=1,
        speaker_review_written=1,
    )
