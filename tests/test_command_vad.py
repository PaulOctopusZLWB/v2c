from __future__ import annotations

from pathlib import Path

import pytest

from personal_context_node.adapters.vad.command import CommandVADAdapter
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


def test_command_vad_adapter_parses_json_ranges(tmp_path: Path) -> None:
    script = tmp_path / "fake_vad.py"
    script.write_text(
        """
import json
import sys

assert sys.argv[1].endswith(".wav")
print(json.dumps({"ranges": [{"start_ms": 100, "end_ms": 900}]}))
""",
        encoding="utf-8",
    )
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = CommandVADAdapter(command=["python3", str(script)]).detect(audio)

    assert result.backend == "CommandVADAdapter"
    assert result.backend_version is None
    assert result.config == {"command": ["python3", str(script)], "merge_gap_ms": 0, "min_speech_ms": 0}
    assert result.warnings == []
    assert [(speech.start_ms, speech.end_ms) for speech in result.ranges] == [(100, 900)]


def test_command_vad_adapter_rejects_invalid_ranges(tmp_path: Path) -> None:
    script = tmp_path / "bad_vad.py"
    script.write_text(
        """
import json
print(json.dumps({"ranges": [{"start_ms": 900, "end_ms": 100}]}))
""",
        encoding="utf-8",
    )
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    with pytest.raises(TerminalPortError, match="invalid VAD range"):
        CommandVADAdapter(command=["python3", str(script)]).detect(audio)


def test_command_vad_adapter_reports_command_failure_as_retryable(tmp_path: Path) -> None:
    script = tmp_path / "failed_vad.py"
    script.write_text("import sys\nsys.stderr.write('device busy')\nsys.exit(9)", encoding="utf-8")
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    with pytest.raises(RetryablePortError, match="device busy"):
        CommandVADAdapter(command=["python3", str(script)]).detect(audio)
