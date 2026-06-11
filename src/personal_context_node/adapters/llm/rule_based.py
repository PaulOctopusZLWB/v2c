from __future__ import annotations

import re

from personal_context_node.core.ports.llm import (
    DailyContext,
    MemoryCandidateDraft,
    SessionDecision,
    SessionSummary,
    SessionTodo,
)


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

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        texts = [str(segment["text"]) for segment in transcript_segments if str(segment["text"]).strip()]
        headline = texts[0] if texts else f"Session {session_id}"
        decisions: list[SessionDecision] = []
        todos: list[SessionTodo] = []
        open_questions: list[str] = []
        for segment in transcript_segments:
            text = str(segment["text"])
            evidence_refs = [str(segment["evidence_id"])]
            if "决定" in text:
                decisions.append(SessionDecision(text=text, evidence_refs=evidence_refs))
            todo = _todo_from_text(text)
            if todo:
                todos.append(SessionTodo(text=todo, owner="self", evidence_refs=evidence_refs))
            if "是否" in text or "？" in text or "?" in text:
                open_questions.append(text)
        return SessionSummary(
            session_id=session_id,
            headline=headline,
            summary=" ".join(texts) if texts else "",
            topics=_topics_from_texts(texts),
            decisions=decisions,
            todos=todos,
            open_questions=open_questions,
        )


def _todo_from_text(text: str) -> str | None:
    if "是否" in text:
        return None
    match = re.search(r"需要(.+?)(?:。|$)", text)
    if not match:
        return None
    return match.group(1).strip(" ，,")


def _topics_from_texts(texts: list[str]) -> list[str]:
    topics: list[str] = []
    for marker, topic in [("ASR", "asr"), ("转写", "转写"), ("协议", "协议"), ("本地", "本地")]:
        if any(marker in text for text in texts):
            topics.append(topic)
    return topics
