from __future__ import annotations

import math
import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import process_once
from personal_context_node.storage.sqlite import connect, fetch_all


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


def test_process_run_group_cli_accepts_explicit_mock_flag(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    result = CliRunner().invoke(
        app,
        [
            "process",
            "run",
            "--mock",
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


def test_process_run_group_cli_uses_command_llm_from_config_for_session_summary(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    config = AppConfig(data_dir=data, obsidian_vault=vault)
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="我决定继续接入真实 ASR。"),
            max_chunk_ms=1000,
        )
    llm_script = tmp_path / "fake_llm.py"
    llm_script.write_text(
        """
import json
import sys
payload = json.loads(sys.stdin.read())
if payload["task"] != "session_summary":
    raise SystemExit(2)
first = payload["transcript_segments"][0]["evidence_id"]
print(json.dumps({
  "headline": "配置 LLM session headline",
  "summary": "配置 LLM session summary",
  "topics": ["asr"],
  "decisions": [{"text": "继续接入真实 ASR", "evidence_refs": [first]}],
  "todos": [],
  "open_questions": []
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data}"
obsidian_vault = "{vault}"

[llm]
backend = "command"
command = "python3 {llm_script}"
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["process", "run", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "task_type=summarize_session" in result.output
    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select content_json from summaries where summary_type = 'session'")
    finally:
        conn.close()
    assert "配置 LLM session headline" in summaries[0]["content_json"]


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
