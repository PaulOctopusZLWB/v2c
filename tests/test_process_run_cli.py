from __future__ import annotations

import math
import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone


def test_process_run_cli_advances_vad_task(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    result = CliRunner().invoke(
        app,
        [
            "process-run",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--vad-threshold",
            "0.05",
            "--max-chunk-ms",
            "1000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "task_type=vad" in result.output
    assert "status=succeeded" in result.output


def test_process_run_group_cli_advances_vad_task(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    result = CliRunner().invoke(
        app,
        [
            "process",
            "run",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--vad-threshold",
            "0.05",
            "--max-chunk-ms",
            "1000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "task_type=vad" in result.output
    assert "status=succeeded" in result.output


def test_process_run_cli_uses_command_vad_backend(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    vad_script = tmp_path / "fake_vad.py"
    vad_script.write_text(
        """
import json
print(json.dumps({"ranges": [{"start_ms": 0, "end_ms": 500}]}))
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "process-run",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--vad-backend",
            "command",
            "--vad-command",
            f"python3 {vad_script}",
            "--max-chunk-ms",
            "1000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "task_type=vad" in result.output
    assert "status=succeeded" in result.output


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
