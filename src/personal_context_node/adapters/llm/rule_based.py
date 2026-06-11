from __future__ import annotations

import re

from personal_context_node.core.ports.llm import DailyContext, MemoryCandidateDraft


class RuleBasedLLMAdapter:
    """Deterministic local text processor used until a real LLM adapter is configured."""

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        texts = [str(segment["text"]) for segment in transcript_segments]
        facts = [text for text in texts if text]
        todos = [_todo_from_text(text) for text in texts if _todo_from_text(text)]
        candidates: list[MemoryCandidateDraft] = []
        for segment in transcript_segments:
            text = str(segment["text"])
            claim_type = "decision" if "决定" in text else "observation"
            candidates.append(
                MemoryCandidateDraft(
                    candidate_claim=text,
                    claim_type=claim_type,
                    confidence=0.6,
                    evidence_source_ids=[str(segment["segment_id"])],
                )
            )
        return DailyContext(
            day=day,
            summary=f"共处理 {len(texts)} 段转写文本。",
            todos=todos,
            facts=facts,
            inferences=[],
            memory_candidates=candidates,
        )


def _todo_from_text(text: str) -> str | None:
    match = re.search(r"需要(.+?)(?:。|$)", text)
    if not match:
        return None
    return match.group(1).strip(" ，,")
