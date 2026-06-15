from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.cli import _build_llm
from personal_context_node.config import AppConfig
from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter


def test_mock_llm_daily_context_comes_from_fixture() -> None:
    fixture = json.loads(Path("src/personal_context_node/fixtures/mock_llm.json").read_text(encoding="utf-8"))
    adapter = MockLLMAdapter()

    context = adapter.generate_daily_context(
        day="2087-05-10",
        transcript_segments=[
            {
                "segment_id": "seg_1",
                "text": "输入文本不应改变 fixture 输出。",
                "evidence_id": "ev_input",
            }
        ],
    )

    assert context.summary == fixture["daily_context"]["summary"]
    assert context.todos == ["输入文本不应改变 fixture 输出。"]
    assert context.facts == fixture["daily_context"]["facts"]
    assert context.inferences == fixture["daily_context"]["inferences"]
    assert context.memory_candidates[0].candidate_claim == fixture["daily_context"]["memory_candidates"][0]["candidate_claim"]
    assert context.memory_candidates[0].evidence_source_ids == ["ev_input"]


def test_mock_llm_daily_todos_are_traceable_to_transcript_text() -> None:
    context = MockLLMAdapter().generate_daily_context(
        day="2087-05-10",
        transcript_segments=[
            {
                "segment_id": "seg_1",
                "text": "fixture 模拟 ASR 转写",
                "evidence_id": "ev_input",
            }
        ],
    )

    assert all(todo in "fixture 模拟 ASR 转写" for todo in context.todos)


def test_build_llm_mock_uses_fixture_adapter() -> None:
    adapter = _build_llm(llm_backend="mock", llm_command=None)

    assert isinstance(adapter, MockLLMAdapter)


def test_default_llm_backend_uses_rule_based_adapter() -> None:
    adapter = _build_llm(llm_backend=AppConfig().llm_backend, llm_command=None)

    assert isinstance(adapter, RuleBasedLLMAdapter)
