from __future__ import annotations

from pathlib import Path

import pytest

from personal_context_node.adapters.vad.command import CommandVADAdapter


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

    ranges = CommandVADAdapter(command=["python3", str(script)]).detect(audio)

    assert [(speech.start_ms, speech.end_ms) for speech in ranges] == [(100, 900)]


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

    with pytest.raises(ValueError, match="invalid VAD range"):
        CommandVADAdapter(command=["python3", str(script)]).detect(audio)
