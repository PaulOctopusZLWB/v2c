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
