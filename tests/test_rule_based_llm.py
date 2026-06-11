from __future__ import annotations

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter


def test_rule_based_llm_extracts_todos_facts_and_candidates() -> None:
    adapter = RuleBasedLLMAdapter()

    context = adapter.generate_daily_context(
        day="2087-05-10",
        transcript_segments=[
            {
                "segment_id": "seg_1",
                "speaker": "self",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "我决定下周继续接入真实 ASR，需要保持音频本地处理。",
                "evidence_id": "ev_1",
            }
        ],
    )

    assert context.day == "2087-05-10"
    assert context.todos == ["保持音频本地处理"]
    assert context.facts == ["我决定下周继续接入真实 ASR，需要保持音频本地处理。"]
    assert context.memory_candidates[0].claim_type == "decision"
    assert context.memory_candidates[0].evidence_source_ids == ["ev_1"]
