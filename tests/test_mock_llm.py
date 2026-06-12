from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.cli import _build_llm
from personal_context_node.adapters.llm.mock import MockLLMAdapter


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
    assert context.todos == fixture["daily_context"]["todos"]
    assert context.facts == fixture["daily_context"]["facts"]
    assert context.inferences == fixture["daily_context"]["inferences"]
    assert context.memory_candidates[0].candidate_claim == fixture["daily_context"]["memory_candidates"][0]["candidate_claim"]
    assert context.memory_candidates[0].evidence_source_ids == ["ev_input"]


def test_build_llm_mock_uses_fixture_adapter() -> None:
    adapter = _build_llm(llm_backend="mock", llm_command=None)

    assert isinstance(adapter, MockLLMAdapter)
