from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


# Closed schemas for `summaries.content_json` (§37.4). extra="forbid" keeps the two
# allowed shapes from silently drifting (e.g. a stray top-level field).


class SummaryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_refs: list[str] = []


class SummaryTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    owner: str
    evidence_refs: list[str] = []


class SummarySpeakerViewpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_refs: list[str] = []


class SummarySpeakerAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_cluster_id: str
    viewpoints: list[SummarySpeakerViewpoint] = []
    sentiment: str = ""
    stance: str = ""
    latent_needs: list[str] = []


class SessionSummarySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["session_summary.v1"] = "session_summary.v1"
    session_id: str
    headline: str
    summary: str
    topics: list[str] = []
    decisions: list[SummaryDecision] = []
    todos: list[SummaryTodo] = []
    open_questions: list[str] = []
    # Per-speaker analytical summary (diarized sessions); empty for non-diarized/rule_based paths.
    core_conclusions: list[str] = []
    per_speaker: list[SummarySpeakerAnalysis] = []


class DailyDecisionRollup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    session_id: str | None = None
    evidence_refs: list[str] = []


class DailyTodoRollup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    owner: str
    session_id: str | None = None
    evidence_refs: list[str] = []


class DailySummarySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["daily_summary.v1"] = "daily_summary.v1"
    date_key: str
    headline: str
    summary: str
    highlights: list[str] = []
    decisions_rollup: list[DailyDecisionRollup] = []
    todos_rollup: list[DailyTodoRollup] = []


def validate_session_summary(content: dict) -> dict:
    return SessionSummarySchema.model_validate(content).model_dump(mode="json")


def validate_daily_summary(content: dict) -> dict:
    return DailySummarySchema.model_validate(content).model_dump(mode="json")
