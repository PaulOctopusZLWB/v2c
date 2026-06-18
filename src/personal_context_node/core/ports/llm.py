from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
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
    subject: dict[str, str] = field(
        default_factory=lambda: {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}
    )


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
class SpeakerViewpoint:
    text: str
    evidence_refs: list[str]


@dataclass(frozen=True)
class SpeakerAnalysis:
    """Per-speaker analysis over a session: the viewpoints they expressed, their sentiment,
    their stance/leaning, and any latent needs — keyed to a diarization cluster id (spk_NN)."""
    speaker_cluster_id: str
    viewpoints: list[SpeakerViewpoint]
    sentiment: str
    stance: str
    latent_needs: list[str]


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    headline: str
    summary: str
    topics: list[str]
    decisions: list[SessionDecision]
    todos: list[SessionTodo]
    open_questions: list[str]
    # Per-speaker analytical summary (diarized sessions). Defaults empty so the rule_based and
    # non-diarized GLM paths — which don't produce per-speaker analysis — stay valid.
    core_conclusions: list[str] = field(default_factory=list)
    per_speaker: list[SpeakerAnalysis] = field(default_factory=list)


class LLMPort(Protocol):
    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        """Generate text-only daily context from transcript segments."""

    def generate_session_summary(
        self,
        *,
        session_id: str,
        transcript_segments: list[dict[str, object]],
        prompt: str | None = None,
    ) -> SessionSummary:
        """Generate a session summary from text-only transcript segments.

        ``prompt`` is an optional editable persona/instruction that replaces the adapter's built-in
        session-summary system text; adapters that don't support a custom prompt accept-and-ignore it.
        """
