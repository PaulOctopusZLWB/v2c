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
    inferences: list[str]
    memory_candidates: list[MemoryCandidateDraft]


class LLMPort(Protocol):
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        """Generate text-only daily context from transcript segments."""
