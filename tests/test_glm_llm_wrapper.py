from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("glm_llm_wrapper", Path("scripts/glm_llm_wrapper.py"))
glm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(glm)


def test_call_glm_extracts_message_content_json() -> None:
    captured = {}

    def fake_post(url, headers, body):
        captured["url"] = url
        captured["auth"] = headers["Authorization"]
        captured["body"] = body
        return {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}

    result = glm.call_glm({"messages": []}, api_key="sk-test", model="glm-4-flash", post=fake_post)

    assert result == {"summary": "ok"}
    assert captured["auth"] == "Bearer sk-test"
    assert "chat/completions" in captured["url"]
    assert captured["body"]["model"] == "glm-4-flash"


def test_normalize_daily_context_constrains_claim_type_and_evidence() -> None:
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "数据不出本机。"}]
    raw = {
        "summary": "讨论本地部署。",
        "todos": ["继续接入模型"],
        "facts": ["音频本地处理"],
        "inferences": [{"type": "inference", "text": "关注证据链", "confidence": 0.7}],
        "memory_candidates": [
            {"candidate_claim": "用户要求音频本地处理。", "claim_type": "SECRET", "confidence": 0.9,
             "evidence_source_ids": ["ev_1", "ev_unknown"]},
            {"candidate_claim": "无证据的候选", "claim_type": "fact", "confidence": 0.5, "evidence_source_ids": []},
        ],
    }

    out = glm.normalize_daily_context(raw, segments)

    assert out["summary"] == "讨论本地部署。"
    # invalid claim_type coerced to the safe default 'observation'
    assert out["memory_candidates"][0]["claim_type"] == "observation"
    # unknown evidence id dropped, valid one kept
    assert out["memory_candidates"][0]["evidence_source_ids"] == ["ev_1"]
    # candidate with no surviving evidence is dropped entirely (adapter would reject it)
    assert len(out["memory_candidates"]) == 1
