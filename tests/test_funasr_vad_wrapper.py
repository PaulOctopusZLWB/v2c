from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_funasr_vad_wrapper_normalizes_value_ranges(tmp_path: Path) -> None:
    fake_package = tmp_path / "fake_package"
    funasr_dir = fake_package / "funasr"
    funasr_dir.mkdir(parents=True)
    (funasr_dir / "__init__.py").write_text(
        """
print("funasr import noise")

class AutoModel:
    def __init__(self, **kwargs):
        print("funasr init noise")
        self.kwargs = kwargs

    def generate(self, input, **kwargs):
        print("funasr generate noise")
        return [{"value": [[100, 900], [1200, 1800]]}]
""",
        encoding="utf-8",
    )
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/funasr_vad_wrapper.py",
            str(audio),
            "--model",
            "fsmn-vad",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(result.stdout) == {
        "ranges": [
            {"start_ms": 100, "end_ms": 900},
            {"start_ms": 1200, "end_ms": 1800},
        ]
    }
    assert result.stderr.splitlines() == ["funasr import noise", "funasr init noise", "funasr generate noise"]


def test_funasr_vad_wrapper_reports_missing_dependency(tmp_path: Path) -> None:
    fake_package = tmp_path / "fake_package"
    funasr_dir = fake_package / "funasr"
    funasr_dir.mkdir(parents=True)
    (funasr_dir / "__init__.py").write_text('raise ImportError("blocked funasr import")\n', encoding="utf-8")
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = subprocess.run(
        [sys.executable, "scripts/funasr_vad_wrapper.py", str(audio)],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "FunASR is not installed" in result.stderr
