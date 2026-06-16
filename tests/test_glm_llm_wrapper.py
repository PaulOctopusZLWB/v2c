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


def test_call_glm_uses_configurable_base_url() -> None:
    captured = {}

    def fake_post(url, headers, body):
        captured["url"] = url
        captured["body"] = body
        return {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}

    base = "http://10.16.12.28:8077/v1"
    result = glm.call_glm(
        {"messages": []}, api_key="sk-test", model="glm-5.1", post=fake_post, base_url=base
    )

    assert result == {"summary": "ok"}
    # endpoint is <base>/chat/completions (trailing slash tolerated)
    assert captured["url"] == "http://10.16.12.28:8077/v1/chat/completions"
    assert captured["body"]["model"] == "glm-5.1"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0.2


def test_call_glm_base_url_strips_trailing_slash() -> None:
    captured = {}

    def fake_post(url, headers, body):
        captured["url"] = url
        return {"choices": [{"message": {"content": "{}"}}]}

    glm.call_glm(
        {"messages": []}, api_key="k", model="m", post=fake_post,
        base_url="http://10.16.12.28:8077/v1/",
    )
    assert captured["url"] == "http://10.16.12.28:8077/v1/chat/completions"


def test_call_glm_thinking_on_adds_chat_template_kwargs() -> None:
    captured = {}

    def fake_post(url, headers, body):
        captured["body"] = body
        return {"choices": [{"message": {"content": "{}"}}]}

    glm.call_glm({"messages": []}, api_key="k", model="m", post=fake_post, thinking=True)
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": True}


def test_call_glm_thinking_off_omits_chat_template_kwargs() -> None:
    captured = {}

    def fake_post(url, headers, body):
        captured["body"] = body
        return {"choices": [{"message": {"content": "{}"}}]}

    glm.call_glm({"messages": []}, api_key="k", model="m", post=fake_post, thinking=False)
    assert "chat_template_kwargs" not in captured["body"]


def test_call_glm_thinking_on_parses_inline_reasoning_content() -> None:
    # thinking-ON server folds reasoning into message.content before the JSON object;
    # call_glm must still recover the JSON via _extract_json.
    def fake_post(url, headers, body):
        return {"choices": [{"message": {"content": '推理过程……\n{"summary": "x"}'}}]}

    result = glm.call_glm({"messages": []}, api_key="k", model="m", post=fake_post, thinking=True)
    assert result == {"summary": "x"}


def test_extract_json_parses_clean_json() -> None:
    assert glm._extract_json('{"summary": "x"}') == {"summary": "x"}


def test_extract_json_strips_think_block() -> None:
    assert glm._extract_json('<think>推理…</think>{"summary":"x"}') == {"summary": "x"}


def test_extract_json_recovers_object_after_inline_reasoning() -> None:
    assert glm._extract_json('推理过程…\n{"a":1}') == {"a": 1}


def test_extract_json_finds_outermost_balanced_object() -> None:
    # nested braces: the outermost { … } must be returned whole, not just the first inner pair
    assert glm._extract_json('think…{"a":{"b":2},"c":3} trailing') == {"a": {"b": 2}, "c": 3}


def test_extract_json_raises_on_non_json() -> None:
    with pytest.raises(Exception):
        glm._extract_json("just some reasoning, no object here")


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


def test_normalize_daily_context_tolerates_malformed_memory_candidates() -> None:
    # GLM (json_object mode) guarantees parseable JSON, not the requested array shape. A
    # memory_candidates that is a bare string, a dict, or a list with non-dict elements must DROP
    # the bad items rather than crash (AttributeError) and fail the whole day via the broad except.
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "x"}]
    base = {"summary": "s", "todos": [], "facts": [], "inferences": []}

    # list containing a plain string element
    out = glm.normalize_daily_context({**base, "memory_candidates": ["I prefer tea"]}, segments)
    assert out["memory_candidates"] == [] and out["summary"] == "s"
    # a dict instead of a list (iterating would otherwise yield string keys)
    out = glm.normalize_daily_context({**base, "memory_candidates": {"candidate_claim": "c"}}, segments)
    assert out["memory_candidates"] == []
    # todos as a non-list must not char-iterate into a list of letters
    out = glm.normalize_daily_context({**base, "todos": "do it", "memory_candidates": []}, segments)
    assert out["todos"] == []


def test_normalize_session_summary_emits_empty_decisions_todos_for_backcompat() -> None:
    # The per-speaker schema replaces decisions/todos with per_speaker viewpoints, but the
    # SessionSummary/adapter contract still carries decisions/todos. normalize must always emit them
    # as empty lists (it no longer parses any raw decisions/todos) so the contract stays satisfied,
    # and it must never raise on a malformed/garbage decisions/todos shape from the model.
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "x"}]
    raw = {
        "headline": "h", "summary": "s", "topics": [],
        "decisions": {"text": "ship it", "evidence_refs": ["ev_1"]},  # bare object, not a list
        "todos": ["just a string"],  # non-dict element
        "open_questions": [],
    }

    out = glm.normalize_session_summary(raw, segments)  # must not raise

    assert out["decisions"] == []
    assert out["todos"] == []
    assert out["headline"] == "h"


def test_normalize_session_summary_builds_per_speaker_and_core_conclusions() -> None:
    segments = [
        {"segment_id": "seg_1", "evidence_id": "ev_1", "speaker": "spk_01", "text": "全部本地处理。"},
        {"segment_id": "seg_2", "evidence_id": "ev_2", "speaker": "spk_02", "text": "成本是个问题。"},
    ]
    raw = {
        "headline": "本地部署推进",
        "core_conclusions": ["团队倾向数据不出本机。", "需评估成本。"],
        "per_speaker": [
            {
                "speaker_cluster_id": "spk_01",
                "viewpoints": [
                    {"text": "主张全部本地处理。", "evidence_refs": ["ev_1"]},
                    {"text": "无证据的观点。", "evidence_refs": ["ev_unknown"]},  # dropped
                ],
                "sentiment": "积极",
                "stance": "支持本地部署、对成本敏感",
                "latent_needs": ["更快的转写速度"],
            },
            {
                "speaker_cluster_id": "spk_02",
                "viewpoints": [{"text": "担心成本上升。", "evidence_refs": ["ev_2", "ev_unknown"]}],
                "sentiment": "谨慎",
                "stance": "关注成本",
                "latent_needs": [],
            },
        ],
        "open_questions": ["是否需要备选模型"],
    }

    out = glm.normalize_session_summary(raw, segments)

    assert out["headline"] == "本地部署推进"
    assert out["core_conclusions"] == ["团队倾向数据不出本机。", "需评估成本。"]
    assert out["open_questions"] == ["是否需要备选模型"]
    # decisions/todos kept as empty lists for the back-compat contract
    assert out["decisions"] == []
    assert out["todos"] == []

    speakers = out["per_speaker"]
    assert [s["speaker_cluster_id"] for s in speakers] == ["spk_01", "spk_02"]
    # spk_01: the unknown-evidence viewpoint is dropped, the valid one survives
    assert [v["text"] for v in speakers[0]["viewpoints"]] == ["主张全部本地处理。"]
    assert speakers[0]["viewpoints"][0]["evidence_refs"] == ["ev_1"]
    assert speakers[0]["sentiment"] == "积极"
    assert speakers[0]["stance"] == "支持本地部署、对成本敏感"
    assert speakers[0]["latent_needs"] == ["更快的转写速度"]
    # spk_02: unknown evidence id filtered out of the surviving viewpoint
    assert speakers[1]["viewpoints"][0]["evidence_refs"] == ["ev_2"]


def test_normalize_session_summary_drops_viewpoints_without_known_evidence() -> None:
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "speaker": "spk_01", "text": "x"}]
    raw = {
        "headline": "h", "core_conclusions": [],
        "per_speaker": [
            {
                "speaker_cluster_id": "spk_01",
                "viewpoints": [{"text": "无证据观点", "evidence_refs": ["ev_x"]}],  # all dropped
                "sentiment": "中立", "stance": "无", "latent_needs": [],
            }
        ],
        "open_questions": [],
    }

    out = glm.normalize_session_summary(raw, segments)

    # the speaker survives with an empty viewpoints list (no viewpoint had a known evidence id)
    assert out["per_speaker"][0]["speaker_cluster_id"] == "spk_01"
    assert out["per_speaker"][0]["viewpoints"] == []


def test_normalize_session_summary_tolerates_malformed_per_speaker() -> None:
    # GLM (json_object mode) may collapse a one-element per_speaker list to a bare object, or emit a
    # list containing a non-dict element. Both must drop to empty rather than crash the summarize task.
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "speaker": "spk_01", "text": "x"}]
    base = {"headline": "h", "core_conclusions": [], "open_questions": []}

    # per_speaker as a dict (not a list)
    out = glm.normalize_session_summary({**base, "per_speaker": {"speaker_cluster_id": "spk_01"}}, segments)
    assert out["per_speaker"] == [] and out["headline"] == "h"
    # per_speaker list containing a non-dict element
    out = glm.normalize_session_summary({**base, "per_speaker": ["not-a-dict"]}, segments)
    assert out["per_speaker"] == []
    # missing per_speaker entirely defaults to empty
    out = glm.normalize_session_summary(base, segments)
    assert out["per_speaker"] == [] and out["core_conclusions"] == []


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
    # The session schema is now PER-SPEAKER: the stub returns the new analytical shape (headline,
    # core_conclusions, per_speaker[…viewpoints/sentiment/stance/latent_needs], open_questions).
    session_stub.write_text(
        "import json\n"
        "def post(url, headers, body):\n"
        "    content = json.dumps({'headline': 'h', 'core_conclusions': ['结论'],\n"
        "        'per_speaker': [{'speaker_cluster_id': 'spk_01',\n"
        "            'viewpoints': [{'text': '主张全部本地处理。', 'evidence_refs': ['ev_1']}],\n"
        "            'sentiment': '积极', 'stance': '支持', 'latent_needs': ['更快的转写速度']}],\n"
        "        'open_questions': ['q']}, ensure_ascii=False)\n"
        "    return {'choices': [{'message': {'content': content}}]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GLM_API_KEY", "sk-test")
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "speaker": "spk_01", "text": "x"}]
    adapter = CommandLLMAdapter(command=[sys.executable, "scripts/glm_llm_wrapper.py"])

    monkeypatch.setenv("GLM_STUB_TRANSPORT", str(daily_stub))
    ctx = adapter.generate_daily_context(day="2026-06-07", transcript_segments=segments)
    # The adapter ACCEPTED the wrapper output (no PortError) -> the daily contract is satisfied.
    assert ctx.memory_candidates[0].candidate_claim == "c"
    assert ctx.memory_candidates[0].claim_type == "fact"

    monkeypatch.setenv("GLM_STUB_TRANSPORT", str(session_stub))
    summary = adapter.generate_session_summary(session_id="ses_1", transcript_segments=segments)
    # The adapter ACCEPTED the wrapper's per-speaker output -> wrapper↔adapter contract agreement.
    assert summary.core_conclusions == ["结论"]
    assert summary.per_speaker[0].speaker_cluster_id == "spk_01"
    assert summary.per_speaker[0].viewpoints[0].text == "主张全部本地处理。"
    assert summary.per_speaker[0].viewpoints[0].evidence_refs == ["ev_1"]
    assert summary.per_speaker[0].sentiment == "积极"
    assert summary.per_speaker[0].latent_needs == ["更快的转写速度"]
    # decisions/todos remain empty lists under the per-speaker schema (back-compat contract)
    assert summary.decisions == []
    assert summary.todos == []


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
