from __future__ import annotations

import json
import re
import subprocess
from typing import get_args

from personal_context_node.adapters.command_runner import run_command
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError
from personal_context_node.core.ports.llm import (
    ClaimType,
    DailyContext,
    MemoryCandidateDraft,
    SessionDecision,
    SessionSummary,
    SessionTodo,
    SpeakerAnalysis,
    SpeakerViewpoint,
)


ALLOWED_CLAIM_TYPES = set(get_args(ClaimType))
RAW_AUDIO_PATH_FIELDS = {"audio_path", "local_raw_path", "raw_audio_path", "source_path", "work_audio_path"}
RAW_AUDIO_PATH_VALUE_RE = re.compile(r"[/\\][^\s\"']+\.(?:wav|wave|m4a|mp3|flac|aac)\b", re.IGNORECASE)


class CommandLLMAdapter:
    """Text-only LLM adapter for local or cloud wrapper commands."""

    def __init__(self, *, command: list[str], timeout_seconds: float = 3600.0) -> None:
        if not command:
            raise ValueError("LLM command must not be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        payload = self._run_json(
            {"task": "daily_context", "day": day, "transcript_segments": _text_only_segments(transcript_segments)}
        )
        _validate_daily_context_payload(payload)
        return DailyContext(
            day=day,
            summary=str(payload["summary"]),
            todos=[str(item) for item in payload["todos"]],
            facts=[str(item) for item in payload["facts"]],
            inferences=[_inference(item) for item in payload["inferences"]],
            memory_candidates=[_memory_candidate(item) for item in payload["memory_candidates"]],
        )

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        payload = self._run_json(
            {
                "task": "session_summary",
                "session_id": session_id,
                "transcript_segments": _text_only_segments(transcript_segments),
            }
        )
        _validate_session_summary_payload(payload)
        return SessionSummary(
            session_id=session_id,
            headline=str(payload["headline"]),
            summary=str(payload["summary"]),
            topics=[str(item) for item in payload["topics"]],
            decisions=[_session_decision(item) for item in payload["decisions"]],
            todos=[_session_todo(item) for item in payload["todos"]],
            open_questions=[str(item) for item in payload["open_questions"]],
            core_conclusions=[str(item) for item in _as_list(payload.get("core_conclusions"))],
            per_speaker=_per_speaker(payload.get("per_speaker")),
        )

    def _run_json(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            completed = run_command(
                self.command,
                stdin_text=json.dumps(payload, ensure_ascii=False),
                timeout_seconds=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RetryablePortError(f"LLM command timed out after {self.timeout_seconds:g}s") from exc
        if completed.returncode != 0:
            raise RetryablePortError(f"LLM command failed with exit {completed.returncode}: {completed.stderr.strip()}")
        try:
            decoded = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"invalid LLM JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise TerminalPortError("LLM output must be an object")
        return decoded


def _text_only_segments(transcript_segments: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_strip_raw_audio_paths(segment) for segment in transcript_segments]


def _strip_raw_audio_paths(value: object) -> object:
    if isinstance(value, dict):
        return {key: _strip_raw_audio_paths(item) for key, item in value.items() if key not in RAW_AUDIO_PATH_FIELDS}
    if isinstance(value, list):
        return [_strip_raw_audio_paths(item) for item in value]
    if isinstance(value, str) and RAW_AUDIO_PATH_VALUE_RE.search(value):
        return RAW_AUDIO_PATH_VALUE_RE.sub("[redacted]", value)
    return value


def _validate_daily_context_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise TerminalPortError("LLM output must be an object")
    for field in ["summary", "todos", "facts", "inferences", "memory_candidates"]:
        if field not in payload:
            raise TerminalPortError(f"LLM output missing required field: {field}")
    for field in ["todos", "facts", "inferences", "memory_candidates"]:
        if not isinstance(payload[field], list):
            raise TerminalPortError(f"LLM output field must be a list: {field}")


def _validate_session_summary_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise TerminalPortError("LLM output must be an object")
    for field in ["headline", "summary", "topics", "decisions", "todos", "open_questions"]:
        if field not in payload:
            raise TerminalPortError(f"LLM session_summary missing required field: {field}")
    for field in ["topics", "decisions", "todos", "open_questions"]:
        if not isinstance(payload[field], list):
            raise TerminalPortError(f"LLM session_summary field must be a list: {field}")


def _session_decision(item: object) -> SessionDecision:
    if not isinstance(item, dict):
        raise TerminalPortError("LLM session_summary decision must be an object")
    for field in ["text", "evidence_refs"]:
        if field not in item:
            raise TerminalPortError(f"LLM session_summary decision missing required field: {field}")
    evidence_refs = item["evidence_refs"]
    return SessionDecision(
        text=str(item["text"]),
        evidence_refs=_evidence_refs(evidence_refs, "LLM session_summary decision evidence_refs"),
    )


def _session_todo(item: object) -> SessionTodo:
    if not isinstance(item, dict):
        raise TerminalPortError("LLM session_summary todo must be an object")
    for field in ["text", "owner", "evidence_refs"]:
        if field not in item:
            raise TerminalPortError(f"LLM session_summary todo missing required field: {field}")
    evidence_refs = item["evidence_refs"]
    return SessionTodo(
        text=str(item["text"]),
        owner=str(item["owner"]),
        evidence_refs=_evidence_refs(evidence_refs, "LLM session_summary todo evidence_refs"),
    )


def _as_list(value: object) -> list[object]:
    # Round-7 hardening: tolerate malformed list shapes (dict-not-list, None, scalars)
    # by treating them as empty rather than raising.
    return value if isinstance(value, list) else []


def _per_speaker(value: object) -> list[SpeakerAnalysis]:
    # A malformed per_speaker (dict-not-list) is tolerated as empty (round-7 lesson);
    # non-dict items are skipped. Only a dict item missing speaker_cluster_id raises.
    return [_speaker_analysis(item) for item in _as_list(value) if isinstance(item, dict)]


def _speaker_analysis(item: dict[str, object]) -> SpeakerAnalysis:
    if "speaker_cluster_id" not in item:
        raise TerminalPortError("LLM session_summary per_speaker missing required field: speaker_cluster_id")
    viewpoints = [_speaker_viewpoint(viewpoint) for viewpoint in _as_list(item.get("viewpoints")) if isinstance(viewpoint, dict)]
    return SpeakerAnalysis(
        speaker_cluster_id=str(item["speaker_cluster_id"]),
        viewpoints=viewpoints,
        sentiment=str(item.get("sentiment", "")),
        stance=str(item.get("stance", "")),
        latent_needs=[str(need) for need in _as_list(item.get("latent_needs"))],
    )


def _speaker_viewpoint(item: dict[str, object]) -> SpeakerViewpoint:
    if "text" not in item:
        raise TerminalPortError("LLM session_summary per_speaker viewpoint missing required field: text")
    return SpeakerViewpoint(
        text=str(item["text"]),
        evidence_refs=_evidence_refs(item.get("evidence_refs"), "LLM session_summary per_speaker viewpoint evidence_refs"),
    )


def _inference(item: object) -> object:
    if isinstance(item, dict):
        if "text" not in item:
            raise TerminalPortError("LLM inference missing required field: text")
        inference_type = str(item.get("type", "inference"))
        if inference_type != "inference":
            raise TerminalPortError(f"LLM inference type must be inference: {inference_type}")
        if "confidence" not in item:
            raise TerminalPortError("LLM inference missing required field: confidence")
        try:
            confidence = float(item["confidence"])
        except (TypeError, ValueError) as exc:
            raise TerminalPortError("LLM inference confidence must be numeric") from exc
        return {"type": inference_type, "text": str(item["text"]), "confidence": confidence}
    return str(item)


def _memory_candidate(item: object) -> MemoryCandidateDraft:
    if not isinstance(item, dict):
        raise TerminalPortError("LLM memory_candidate must be an object")
    for field in ["candidate_claim", "claim_type", "confidence"]:
        if field not in item:
            raise TerminalPortError(f"LLM memory_candidate missing required field: {field}")
    if "evidence_refs" not in item and "evidence_source_ids" not in item:
        raise TerminalPortError("LLM memory_candidate missing required field: evidence_refs")
    claim_type = str(item["claim_type"])
    if claim_type not in ALLOWED_CLAIM_TYPES:
        raise TerminalPortError(f"LLM memory_candidate has invalid claim_type: {claim_type}")
    evidence_source_ids = item.get("evidence_refs", item.get("evidence_source_ids"))
    evidence_refs = _evidence_refs(evidence_source_ids, "LLM memory_candidate evidence_refs")
    try:
        confidence = float(item["confidence"])
    except (TypeError, ValueError) as exc:
        raise TerminalPortError("LLM memory_candidate confidence must be numeric") from exc
    return MemoryCandidateDraft(
        candidate_claim=str(item["candidate_claim"]),
        claim_type=claim_type,
        confidence=confidence,
        evidence_source_ids=evidence_refs,
        subject=_subject(item.get("subject")),
    )


def _evidence_refs(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise TerminalPortError(f"{label} must be a list")
    refs = [str(ref).strip() for ref in value]
    if not refs or any(not ref for ref in refs):
        raise TerminalPortError(f"{label} must not be empty")
    return refs


def _subject(item: object) -> dict[str, str]:
    if item is None:
        return {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}
    if not isinstance(item, dict):
        raise TerminalPortError("LLM memory_candidate subject must be an object")
    for field in ["type", "id", "label"]:
        if field not in item:
            raise TerminalPortError(f"LLM memory_candidate subject missing required field: {field}")
        if not str(item[field]).strip():
            raise TerminalPortError(f"LLM memory_candidate subject {field} must not be empty")
    return {"type": str(item["type"]), "id": str(item["id"]), "label": str(item["label"])}
