from __future__ import annotations

import math
import stat
import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone


def test_transcribe_cli_uses_command_asr_backend(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        max_chunk_ms=1000,
    )
    script = tmp_path / "fake_asr.py"
    script.write_text(
        """
import json
import sys
print(json.dumps({
  "model_name": "sensevoice",
  "model_version": "wrapper-test",
  "segments": [{"text": "命令式 ASR 输出", "start_ms": 0, "end_ms": 500, "confidence": 0.9, "language": "zh"}]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--asr-backend",
            "command",
            "--asr-command",
            f"python3 {script}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "chunks_transcribed=1" in result.output
    assert "segments_created=1" in result.output


def _write_voice_wav(path: Path, seconds: float = 0.7, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(seconds * sample_rate)):
            sample = int(10_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
