from __future__ import annotations

import json
from importlib.resources import files

from personal_context_node.core.ports.llm import (
    DailyContext,
    MemoryCandidateDraft,
    SessionDecision,
    SessionSummary,
    SessionTodo,
)


class MockLLMAdapter:
    """Fixture-backed deterministic LLM adapter for E2E and CLI smoke tests."""

    def __init__(self, *, fixture: dict[str, object] | None = None) -> None:
        self.fixture = fixture or _mock_llm_fixture()

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        daily = _object_dict(self.fixture["daily_context"], "daily_context")
        evidence_id = _first_evidence_id(transcript_segments)
        return DailyContext(
            day=day,
            summary=str(daily["summary"]),
            todos=[str(item) for item in _object_list(daily["todos"], "daily_context.todos")],
            facts=[str(item) for item in _object_list(daily["facts"], "daily_context.facts")],
            inferences=_object_list(daily["inferences"], "daily_context.inferences"),
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim=str(item["candidate_claim"]),
                    claim_type=item["claim_type"],
                    confidence=float(item["confidence"]),
                    evidence_source_ids=[evidence_id],
                )
                for item in (
                    _object_dict(raw, "daily_context.memory_candidates item")
                    for raw in _object_list(daily["memory_candidates"], "daily_context.memory_candidates")
                )
            ],
        )

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        session = _object_dict(self.fixture["session_summary"], "session_summary")
        evidence_id = _first_evidence_id(transcript_segments)
        return SessionSummary(
            session_id=session_id,
            headline=str(session["headline"]),
            summary=str(session["summary"]),
            topics=[str(item) for item in _object_list(session["topics"], "session_summary.topics")],
            decisions=[
                SessionDecision(text=str(item["text"]), evidence_refs=[evidence_id])
                for item in (
                    _object_dict(raw, "session_summary.decisions item")
                    for raw in _object_list(session["decisions"], "session_summary.decisions")
                )
            ],
            todos=[
                SessionTodo(text=str(item["text"]), owner=str(item["owner"]), evidence_refs=[evidence_id])
                for item in (
                    _object_dict(raw, "session_summary.todos item")
                    for raw in _object_list(session["todos"], "session_summary.todos")
                )
            ],
            open_questions=[str(item) for item in _object_list(session["open_questions"], "session_summary.open_questions")],
        )


def _mock_llm_fixture() -> dict[str, object]:
    fixture_path = files("personal_context_node").joinpath("fixtures/mock_llm.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _first_evidence_id(transcript_segments: list[dict[str, object]]) -> str:
    if not transcript_segments:
        return "ev_fixture"
    return str(transcript_segments[0]["evidence_id"])


def _object_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _object_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value
