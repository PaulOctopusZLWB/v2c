from __future__ import annotations

import stat
from pathlib import Path

from personal_context_node.adapters.asr.command import CommandASRAdapter
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


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

    result = adapter.transcribe(chunk)

    assert result.backend == "CommandASRAdapter"
    assert result.model_name == "sensevoice"
    assert result.model_version == "local-test"
    assert result.decode_config == {"command": ["python3", str(script)]}
    assert result.warnings == []
    assert result.segments[0].text == "真实 ASR wrapper 输出"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 1200
    assert result.segments[0].confidence == 0.88


def test_command_asr_adapter_ignores_extra_segment_fields_from_model_wrappers(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "speaker_asr.py"
    script.write_text(
        """
import json
print(json.dumps({
  "model_name": "sensevoice",
  "model_version": "local-test",
  "segments": [
    {"text": "带 speaker 的输出", "start_ms": 0, "end_ms": 1200, "language": "zh", "speaker": "spk0"}
  ]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )

    result = CommandASRAdapter(command=["python3", str(script)]).transcribe(chunk)

    assert result.segments[0].text == "带 speaker 的输出"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 1200
    assert result.segments[0].language == "zh"


def test_command_asr_adapter_preserves_segment_tags_from_model_wrappers(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "tagged_asr.py"
    script.write_text(
        """
import json
print(json.dumps({
  "model_name": "sensevoice",
  "model_version": "local-test",
  "segments": [
    {"text": "Yeah.", "start_ms": 0, "end_ms": 800, "language": "zh", "tags": ["yue", "EMO_UNKNOWN", "Speech", "withitn"]}
  ]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )

    result = CommandASRAdapter(command=["python3", str(script)]).transcribe(chunk)

    assert result.segments[0].text == "Yeah."
    assert result.segments[0].tags == ["yue", "EMO_UNKNOWN", "Speech", "withitn"]


def test_command_asr_adapter_reports_invalid_output(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "bad_asr.py"
    script.write_text("print('not json')", encoding="utf-8")

    adapter = CommandASRAdapter(command=["python3", str(script)])

    try:
        adapter.transcribe(chunk)
    except TerminalPortError as exc:
        assert "invalid ASR JSON" in str(exc)
    else:
        raise AssertionError("CommandASRAdapter accepted invalid JSON")


def test_command_asr_adapter_reports_command_failure_as_retryable(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "failed_asr.py"
    script.write_text("import sys\nsys.stderr.write('model busy')\nsys.exit(7)", encoding="utf-8")

    adapter = CommandASRAdapter(command=["python3", str(script)])

    try:
        adapter.transcribe(chunk)
    except RetryablePortError as exc:
        assert "model busy" in str(exc)
    else:
        raise AssertionError("CommandASRAdapter accepted a failed command")


def test_command_asr_adapter_maps_terminal_exit_code_to_terminal_error(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "terminal_asr.py"
    # Exit code 3 = permanently unsupported input (§28.3.4).
    script.write_text("import sys\nsys.stderr.write('unsupported format')\nsys.exit(3)", encoding="utf-8")

    adapter = CommandASRAdapter(command=["python3", str(script)])

    try:
        adapter.transcribe(chunk)
    except TerminalPortError as exc:
        assert "permanently unsupported" in str(exc)
    else:
        raise AssertionError("CommandASRAdapter retried a permanently unsupported input")
