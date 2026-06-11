from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_funasr_sensevoice_wrapper_normalizes_sentence_info(tmp_path: Path) -> None:
    fake_package = tmp_path / "fake_package"
    funasr_dir = fake_package / "funasr"
    funasr_dir.mkdir(parents=True)
    (funasr_dir / "__init__.py").write_text(
        """
class AutoModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate(self, input, **kwargs):
        return [{
            "text": "完整文本",
            "sentence_info": [
                {"text": "第一句", "start": 0, "end": 1200, "spk": "spk0"},
                {"text": "第二句", "timestamp": [1200, 2400], "speaker": "spk1"},
            ],
        }]
""",
        encoding="utf-8",
    )
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/funasr_sensevoice_wrapper.py",
            str(audio),
            "--model",
            "iic/SenseVoiceSmall",
            "--model-version",
            "test-version",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "model_name": "sensevoice",
        "model_version": "test-version",
        "segments": [
            {
                "text": "第一句",
                "start_ms": 0,
                "end_ms": 1200,
                "confidence": None,
                "language": "zh",
                "speaker": "spk0",
            },
            {
                "text": "第二句",
                "start_ms": 1200,
                "end_ms": 2400,
                "confidence": None,
                "language": "zh",
                "speaker": "spk1",
            },
        ],
    }


def test_funasr_sensevoice_wrapper_reports_missing_dependency(tmp_path: Path) -> None:
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = subprocess.run(
        [sys.executable, "scripts/funasr_sensevoice_wrapper.py", str(audio)],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(tmp_path / "empty")},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "FunASR is not installed" in result.stderr
