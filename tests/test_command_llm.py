from __future__ import annotations

import json
import stat
from pathlib import Path

from personal_context_node.adapters.llm.command import CommandLLMAdapter
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


def test_command_llm_adapter_sends_text_only_and_parses_context(tmp_path: Path) -> None:
    capture = tmp_path / "input.json"
    script = tmp_path / "fake_llm.py"
    script.write_text(
        f"""
import json
import sys
payload = json.loads(sys.stdin.read())
open({str(capture)!r}, "w").write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
print(json.dumps({{
  "summary": "命令式 LLM 摘要",
  "todos": ["继续接入真实模型"],
  "facts": ["音频本地处理"],
  "inferences": ["关注证据链"],
  "memory_candidates": [
    {{
      "candidate_claim": "用户要求音频本地处理。",
      "claim_type": "requirement",
      "confidence": 0.9,
      "evidence_source_ids": ["seg_1"]
    }}
  ]
}}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    context = adapter.generate_daily_context(
        day="2087-05-10",
        transcript_segments=[
            {
                "segment_id": "seg_1",
                "speaker": "self",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "音频必须本地处理。",
                "evidence_id": "ev_1",
            }
        ],
    )

    assert context.summary == "命令式 LLM 摘要"
    assert context.memory_candidates[0].evidence_source_ids == ["seg_1"]
    sent = json.loads(capture.read_text(encoding="utf-8"))
    assert sent["day"] == "2087-05-10"
    assert "local_raw_path" not in json.dumps(sent)
    assert ".wav" not in json.dumps(sent).lower()


def test_command_llm_adapter_strips_raw_audio_paths_from_input(tmp_path: Path) -> None:
    capture = tmp_path / "input.json"
    script = tmp_path / "fake_llm.py"
    script.write_text(
        f"""
import json
import sys
payload = json.loads(sys.stdin.read())
open({str(capture)!r}, "w").write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
print(json.dumps({{
  "summary": "命令式 LLM 摘要",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": []
}}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    adapter.generate_daily_context(
        day="2087-05-10",
        transcript_segments=[
            {
                "segment_id": "seg_1",
                "evidence_id": "ev_1",
                "text": "音频必须本地处理。",
                "local_raw_path": "/private/audio/TX02_MIC001_20870510_173550_orig.wav",
                "raw_audio_path": "/private/audio/raw.wav",
                "audio_path": "/private/audio/work.wav",
            }
        ],
    )

    sent = json.loads(capture.read_text(encoding="utf-8"))
    serialized = json.dumps(sent, ensure_ascii=False)
    assert "raw_audio_path" not in serialized
    assert "local_raw_path" not in serialized
    assert "audio_path" not in serialized
    assert ".wav" not in serialized.lower()


def test_command_llm_adapter_accepts_design_evidence_refs_field(tmp_path: Path) -> None:
    script = tmp_path / "fake_llm.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "命令式 LLM 摘要",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [
    {
      "candidate_claim": "用户要求音频本地处理。",
      "claim_type": "requirement",
      "subject": {"type": "project", "id": "project_audio", "label": "Audio Project"},
      "confidence": 0.9,
      "evidence_refs": ["ev_1"]
    }
  ]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    context = adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])

    assert context.memory_candidates[0].evidence_source_ids == ["ev_1"]
    assert context.memory_candidates[0].subject == {
        "type": "project",
        "id": "project_audio",
        "label": "Audio Project",
    }


def test_command_llm_adapter_preserves_structured_inferences(tmp_path: Path) -> None:
    script = tmp_path / "fake_llm.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "命令式 LLM 摘要",
  "todos": [],
  "facts": [],
  "inferences": [{"type": "inference", "text": "用户关注证据链", "confidence": 0.72}],
  "memory_candidates": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    context = adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])

    assert context.inferences == [{"type": "inference", "text": "用户关注证据链", "confidence": 0.72}]


def test_command_llm_adapter_rejects_non_inference_structured_inference(tmp_path: Path) -> None:
    script = tmp_path / "bad_inference_type.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": [],
  "facts": [],
  "inferences": [{"type": "fact", "text": "用户关注证据链", "confidence": 0.72}],
  "memory_candidates": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "inference type" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a structured inference with non-inference type")


def test_command_llm_adapter_generates_session_summary(tmp_path: Path) -> None:
    capture = tmp_path / "session_input.json"
    script = tmp_path / "fake_session_llm.py"
    script.write_text(
        f"""
import json
import sys
payload = json.loads(sys.stdin.read())
open({str(capture)!r}, "w").write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
first = payload["transcript_segments"][0]["evidence_id"]
print(json.dumps({{
  "headline": "命令式 session headline",
  "summary": "命令式 session summary",
  "topics": ["asr"],
  "decisions": [{{"text": "继续本地 ASR", "evidence_refs": [first]}}],
  "todos": [{{"text": "完成 smoke test", "owner": "self", "evidence_refs": [first]}}],
  "open_questions": ["是否需要备选模型"]
}}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    summary = adapter.generate_session_summary(
        session_id="ses_1",
        transcript_segments=[
            {
                "segment_id": "seg_1",
                "speaker": "self",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "继续本地 ASR。",
                "evidence_id": "ev_1",
            }
        ],
    )

    assert summary.session_id == "ses_1"
    assert summary.headline == "命令式 session headline"
    assert summary.summary == "命令式 session summary"
    assert summary.topics == ["asr"]
    assert summary.decisions[0].text == "继续本地 ASR"
    assert summary.decisions[0].evidence_refs == ["ev_1"]
    assert summary.todos[0].text == "完成 smoke test"
    assert summary.todos[0].owner == "self"
    assert summary.todos[0].evidence_refs == ["ev_1"]
    assert summary.open_questions == ["是否需要备选模型"]
    sent = json.loads(capture.read_text(encoding="utf-8"))
    assert sent["task"] == "session_summary"
    assert sent["session_id"] == "ses_1"
    assert "local_raw_path" not in json.dumps(sent)
    assert ".wav" not in json.dumps(sent).lower()


def test_command_llm_adapter_rejects_incomplete_session_summary(tmp_path: Path) -> None:
    script = tmp_path / "bad_session_llm.py"
    script.write_text(
        """
import json
print(json.dumps({
  "headline": "bad",
  "summary": "missing collections"
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_session_summary(session_id="ses_1", transcript_segments=[])
    except TerminalPortError as exc:
        assert "topics" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted an incomplete session summary")


def test_command_llm_adapter_reports_invalid_json(tmp_path: Path) -> None:
    script = tmp_path / "bad_llm.py"
    script.write_text("print('not json')", encoding="utf-8")
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "invalid LLM JSON" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted invalid JSON")


def test_command_llm_adapter_reports_command_failure_as_retryable(tmp_path: Path) -> None:
    script = tmp_path / "failed_llm.py"
    script.write_text("import sys\nsys.stderr.write('rate limited')\nsys.exit(8)", encoding="utf-8")
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except RetryablePortError as exc:
        assert "rate limited" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a failed command")


def test_command_llm_adapter_rejects_missing_candidate_fields(tmp_path: Path) -> None:
    script = tmp_path / "missing_candidate_field.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [{"candidate_claim": "缺字段", "claim_type": "requirement"}]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted an incomplete memory candidate")


def test_command_llm_adapter_rejects_missing_top_level_fields(tmp_path: Path) -> None:
    script = tmp_path / "missing_top_level.py"
    script.write_text(
        """
import json
print(json.dumps({
  "memory_candidates": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "summary" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted missing top-level LLM fields")


def test_command_llm_adapter_rejects_invalid_top_level_list_fields(tmp_path: Path) -> None:
    script = tmp_path / "bad_list_field.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": "not-a-list",
  "facts": [],
  "inferences": [],
  "memory_candidates": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "todos" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a non-list top-level field")


def test_command_llm_adapter_rejects_invalid_claim_type(tmp_path: Path) -> None:
    script = tmp_path / "invalid_claim_type.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [{
    "candidate_claim": "非法类型",
    "claim_type": "secret",
    "confidence": 0.9,
    "evidence_source_ids": []
  }]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "claim_type" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted an invalid claim_type")


def test_command_llm_adapter_rejects_empty_candidate_evidence_refs(tmp_path: Path) -> None:
    script = tmp_path / "empty_candidate_evidence.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [{
    "candidate_claim": "缺少证据",
    "claim_type": "requirement",
    "confidence": 0.9,
    "evidence_refs": []
  }]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "evidence_refs" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a candidate without evidence refs")


def test_command_llm_adapter_rejects_blank_candidate_evidence_refs(tmp_path: Path) -> None:
    script = tmp_path / "blank_candidate_evidence.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [{
    "candidate_claim": "空白证据",
    "claim_type": "requirement",
    "confidence": 0.9,
    "evidence_refs": [" "]
  }]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "evidence_refs" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a blank candidate evidence ref")


def test_command_llm_adapter_rejects_empty_candidate_subject_fields(tmp_path: Path) -> None:
    script = tmp_path / "empty_subject.py"
    script.write_text(
        """
import json
print(json.dumps({
  "summary": "bad",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [{
    "candidate_claim": "主体为空",
    "claim_type": "requirement",
    "subject": {"type": "project", "id": " ", "label": "Project"},
    "confidence": 0.9,
    "evidence_refs": ["ev_1"]
  }]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
    except TerminalPortError as exc:
        assert "subject id" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted an empty subject id")


def test_command_llm_adapter_rejects_empty_session_decision_evidence_refs(tmp_path: Path) -> None:
    script = tmp_path / "empty_session_decision_evidence.py"
    script.write_text(
        """
import json
print(json.dumps({
  "headline": "bad",
  "summary": "bad",
  "topics": [],
  "decisions": [{"text": "缺少证据", "evidence_refs": []}],
  "todos": [],
  "open_questions": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_session_summary(session_id="ses_1", transcript_segments=[])
    except TerminalPortError as exc:
        assert "evidence_refs" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a session decision without evidence refs")


def test_command_llm_adapter_rejects_blank_session_todo_evidence_refs(tmp_path: Path) -> None:
    script = tmp_path / "blank_session_todo_evidence.py"
    script.write_text(
        """
import json
print(json.dumps({
  "headline": "bad",
  "summary": "bad",
  "topics": [],
  "decisions": [],
  "todos": [{"text": "缺少证据", "owner": "self", "evidence_refs": [" "]}],
  "open_questions": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandLLMAdapter(command=["python3", str(script)])

    try:
        adapter.generate_session_summary(session_id="ses_1", transcript_segments=[])
    except TerminalPortError as exc:
        assert "evidence_refs" in str(exc)
    else:
        raise AssertionError("CommandLLMAdapter accepted a blank session todo evidence ref")
