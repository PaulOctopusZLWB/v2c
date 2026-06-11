from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


ClaimType = Literal[
    "fact",
    "preference",
    "decision",
    "commitment",
    "requirement",
    "observation",
    "todo",
    "relationship",
]


@dataclass(frozen=True)
class MemoryCandidateDraft:
    candidate_claim: str
    claim_type: ClaimType
    confidence: float
    evidence_source_ids: list[str]


@dataclass(frozen=True)
class DailyContext:
    day: str
    summary: str
    todos: list[str]
    facts: list[str]
    inferences: list[object]
    memory_candidates: list[MemoryCandidateDraft]


@dataclass(frozen=True)
class SessionDecision:
    text: str
    evidence_refs: list[str]


@dataclass(frozen=True)
class SessionTodo:
    text: str
    owner: str
    evidence_refs: list[str]


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    headline: str
    summary: str
    topics: list[str]
    decisions: list[SessionDecision]
    todos: list[SessionTodo]
    open_questions: list[str]


class LLMPort(Protocol):
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        """Generate text-only daily context from transcript segments."""

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        """Generate a session summary from text-only transcript segments."""
