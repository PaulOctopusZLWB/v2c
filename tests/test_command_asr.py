from __future__ import annotations

import json
import stat
from pathlib import Path

from personal_context_node.adapters.asr.command import CommandASRAdapter


def test_command_asr_adapter_parses_json_segments(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "fake_asr.py"
    script.write_text(
        """
import json
import sys
assert sys.argv[1].endswith("chunk.wav")
print(json.dumps({
  "model_name": "sensevoice",
  "model_version": "local-test",
  "segments": [
    {"text": "真实 ASR wrapper 输出", "start_ms": 0, "end_ms": 1200, "confidence": 0.88, "language": "zh"}
  ]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    adapter = CommandASRAdapter(command=["python3", str(script)])

    segments = adapter.transcribe(chunk)

    assert adapter.model_name == "sensevoice"
    assert adapter.model_version == "local-test"
    assert segments[0].text == "真实 ASR wrapper 输出"
    assert segments[0].start_ms == 0
    assert segments[0].end_ms == 1200
    assert segments[0].confidence == 0.88


def test_command_asr_adapter_reports_invalid_output(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "bad_asr.py"
    script.write_text("print('not json')", encoding="utf-8")

    adapter = CommandASRAdapter(command=["python3", str(script)])

    try:
        adapter.transcribe(chunk)
    except ValueError as exc:
        assert "invalid ASR JSON" in str(exc)
    else:
        raise AssertionError("CommandASRAdapter accepted invalid JSON")
