from __future__ import annotations

import json
import subprocess
from typing import get_args

from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError
from personal_context_node.core.ports.llm import ClaimType, DailyContext, MemoryCandidateDraft


ALLOWED_CLAIM_TYPES = set(get_args(ClaimType))


class CommandLLMAdapter:
    """Text-only LLM adapter for local or cloud wrapper commands."""

    def __init__(self, *, command: list[str]) -> None:
        if not command:
            raise ValueError("LLM command must not be empty")
        self.command = command

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        completed = subprocess.run(
            self.command,
            input=json.dumps({"day": day, "transcript_segments": transcript_segments}, ensure_ascii=False),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RetryablePortError(f"LLM command failed with exit {completed.returncode}: {completed.stderr.strip()}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"invalid LLM JSON: {exc}") from exc
        return DailyContext(
            day=day,
            summary=str(payload.get("summary", "")),
            todos=[str(item) for item in payload.get("todos", [])],
            facts=[str(item) for item in payload.get("facts", [])],
            inferences=[str(item) for item in payload.get("inferences", [])],
            memory_candidates=[_memory_candidate(item) for item in payload.get("memory_candidates", [])],
        )


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
    if not isinstance(evidence_source_ids, list):
        raise TerminalPortError("LLM memory_candidate evidence_source_ids must be a list")
    try:
        confidence = float(item["confidence"])
    except (TypeError, ValueError) as exc:
        raise TerminalPortError("LLM memory_candidate confidence must be numeric") from exc
    return MemoryCandidateDraft(
        candidate_claim=str(item["candidate_claim"]),
        claim_type=claim_type,
        confidence=confidence,
        evidence_source_ids=[str(source_id) for source_id in evidence_source_ids],
    )
