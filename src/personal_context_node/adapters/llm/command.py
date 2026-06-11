from __future__ import annotations

import json
import subprocess

from personal_context_node.core.ports.llm import DailyContext, MemoryCandidateDraft


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
            raise RuntimeError(f"LLM command failed with exit {completed.returncode}: {completed.stderr.strip()}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid LLM JSON: {exc}") from exc
        return DailyContext(
            day=day,
            summary=str(payload.get("summary", "")),
            todos=[str(item) for item in payload.get("todos", [])],
            facts=[str(item) for item in payload.get("facts", [])],
            inferences=[str(item) for item in payload.get("inferences", [])],
            memory_candidates=[
                MemoryCandidateDraft(
                    candidate_claim=str(item["candidate_claim"]),
                    claim_type=item["claim_type"],
                    confidence=float(item["confidence"]),
                    evidence_source_ids=[str(source_id) for source_id in item["evidence_source_ids"]],
                )
                for item in payload.get("memory_candidates", [])
            ],
        )
