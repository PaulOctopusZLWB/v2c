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
    # response_format json_object is the named guarantee that forces GLM to emit a parseable JSON
    # object (call_glm does json.loads on the content); pin it + the low temperature so dropping
    # either from the request body fails here instead of only blowing up against the live API.
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0.2


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


def test_normalize_session_summary_drops_decisions_without_known_evidence() -> None:
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "继续本地 ASR。"}]
    raw = {
        "headline": "本地 ASR 推进", "summary": "讨论本地转写。", "topics": ["asr"],
        "decisions": [{"text": "继续本地 ASR", "evidence_refs": ["ev_1"]},
                      {"text": "无证据决定", "evidence_refs": ["ev_x"]}],
        "todos": [{"text": "完成 smoke", "owner": "self", "evidence_refs": ["ev_1"]}],
        "open_questions": ["是否需要备选模型"],
    }

    out = glm.normalize_session_summary(raw, segments)

    assert out["headline"] == "本地 ASR 推进"
    assert [d["text"] for d in out["decisions"]] == ["继续本地 ASR"]  # ev_x dropped
    assert out["todos"][0]["owner"] == "self"


import json
import os
import subprocess
import sys


def test_main_emits_contract_json_via_stubbed_transport(tmp_path) -> None:
    # A fake transport module that returns canned GLM responses; injected via env so the
    # script runs as a real subprocess (the path CommandLLMAdapter uses).
    stub = tmp_path / "glm_stub.py"
    stub.write_text(
        "def post(url, headers, body):\n"
        "    return {'choices': [{'message': {'content': '"
        '{"summary":"日报","todos":[],"facts":[],"inferences":[],'
        '"memory_candidates":[{\\"candidate_claim\\":\\"c\\",\\"claim_type\\":\\"fact\\",'
        '\\"confidence\\":0.9,\\"evidence_source_ids\\":[\\"ev_1\\"]}]}'
        "'}}]}\n",
        encoding="utf-8",
    )
    payload = {"task": "daily_context", "day": "2026-06-07",
               "transcript_segments": [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "x"}]}
    env = {**os.environ, "GLM_API_KEY": "sk-test", "GLM_STUB_TRANSPORT": str(stub)}

    proc = subprocess.run([sys.executable, "scripts/glm_llm_wrapper.py"], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["summary"] == "日报"
    assert out["memory_candidates"][0]["evidence_source_ids"] == ["ev_1"]


def test_glm_wrapper_output_satisfies_real_command_llm_adapter_contract(tmp_path, monkeypatch) -> None:
    # The wrapper's normalize_* tests are self-referential (they assert against the wrapper's own
    # output). This drives the wrapper through the AUTHORITATIVE validator — CommandLLMAdapter,
    # which run_command invokes as a subprocess inheriting os.environ — so any contract drift
    # (e.g. a dropped candidate_claim/owner/required field) surfaces as the adapter raising a
    # Terminal/RetryablePortError instead of silently passing the wrapper's hand-mirrored asserts.
    from personal_context_node.adapters.llm.command import CommandLLMAdapter

    daily_stub = tmp_path / "daily_stub.py"
    daily_stub.write_text(
        "import json\n"
        "def post(url, headers, body):\n"
        "    content = json.dumps({'summary': '日报', 'todos': ['t'], 'facts': ['f'],\n"
        "        'inferences': [{'type': 'inference', 'text': 'i', 'confidence': 0.7}],\n"
        "        'memory_candidates': [{'candidate_claim': 'c', 'claim_type': 'fact',\n"
        "            'confidence': 0.9, 'evidence_source_ids': ['ev_1']}]}, ensure_ascii=False)\n"
        "    return {'choices': [{'message': {'content': content}}]}\n",
        encoding="utf-8",
    )
    session_stub = tmp_path / "session_stub.py"
    session_stub.write_text(
        "import json\n"
        "def post(url, headers, body):\n"
        "    content = json.dumps({'headline': 'h', 'summary': 's', 'topics': ['x'],\n"
        "        'decisions': [{'text': 'd', 'evidence_refs': ['ev_1']}],\n"
        "        'todos': [{'text': 't', 'owner': 'self', 'evidence_refs': ['ev_1']}],\n"
        "        'open_questions': ['q']}, ensure_ascii=False)\n"
        "    return {'choices': [{'message': {'content': content}}]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GLM_API_KEY", "sk-test")
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "x"}]
    adapter = CommandLLMAdapter(command=[sys.executable, "scripts/glm_llm_wrapper.py"])

    monkeypatch.setenv("GLM_STUB_TRANSPORT", str(daily_stub))
    ctx = adapter.generate_daily_context(day="2026-06-07", transcript_segments=segments)
    # The adapter ACCEPTED the wrapper output (no PortError) -> the daily contract is satisfied.
    assert ctx.memory_candidates[0].candidate_claim == "c"
    assert ctx.memory_candidates[0].claim_type == "fact"

    monkeypatch.setenv("GLM_STUB_TRANSPORT", str(session_stub))
    summary = adapter.generate_session_summary(session_id="ses_1", transcript_segments=segments)
    assert summary.todos[0].owner == "self"
    assert summary.decisions[0].evidence_refs == ["ev_1"]


def test_main_fails_retryable_without_api_key() -> None:
    env = {k: v for k, v in os.environ.items() if k != "GLM_API_KEY"}
    proc = subprocess.run([sys.executable, "scripts/glm_llm_wrapper.py"],
                          input='{"task":"daily_context","day":"2026-06-07","transcript_segments":[]}',
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0
    assert "GLM_API_KEY" in proc.stderr


def test_normalize_daily_context_tolerates_non_numeric_confidence() -> None:
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "x"}]
    raw = {
        "summary": "s", "todos": [], "facts": [],
        "inferences": [{"type": "inference", "text": "i", "confidence": "very high"}],
        "memory_candidates": [
            {"candidate_claim": "c", "claim_type": "fact", "confidence": "high", "evidence_source_ids": ["ev_1"]}
        ],
    }

    out = glm.normalize_daily_context(raw, segments)  # must not raise

    assert out["memory_candidates"][0]["confidence"] == 0.5
    assert out["inferences"][0]["confidence"] == 0.5
