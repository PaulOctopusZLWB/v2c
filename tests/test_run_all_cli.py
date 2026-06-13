from __future__ import annotations

import math
import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all


def test_run_all_cli_imports_processes_and_publishes_session_transcript(tmp_path: Path) -> None:
    source_dir = tmp_path / "NO NAME"
    _write_voice_wav(source_dir / "TX02_MIC001_20870510_173550_orig.wav", seconds=1.2)
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[device.dji_mic_3]
root_path = "{source_dir}"
volume_name_patterns = ["NO NAME"]
stable_seconds = 0

[vad]
backend = "energy"
threshold = 1.0
min_speech_ms = 100
merge_gap_ms = 100
max_chunk_ms = 1000
chunk_overlap_ms = 0

[asr]
backend = "command"
command = "python3 missing_asr.py"

[llm]
backend = "rule_based"

[obsidian]
edit_grace_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "run-all",
            "--config",
            str(config_path),
            "--mock",
            "--mock-text",
            "run-all 转写文本",
            "--max-steps",
            "20",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    assert "process_steps=6" in result.output
    assert "tasks_succeeded=6" in result.output
    assert "status=complete" in result.output

    session_notes = sorted((vault / "20_Conversations" / "2025-06-10").glob("ses_*.md"))
    assert len(session_notes) == 1
    session_text = session_notes[0].read_text(encoding="utf-8")
    assert "## Transcript" in session_text
    assert "run-all 转写文本" in session_text
    assert (vault / "10_Daily" / "2025-06-10.md").exists()
    assert (vault / "30_Memory_Candidates" / "2025-06-10.md").exists()

    config = AppConfig.from_toml(config_path)
    conn = connect(config.database_path)
    try:
        task_rows = fetch_all(conn, "select status from tasks order by task_type")
    finally:
        conn.close()
    assert task_rows
    assert {row["status"] for row in task_rows} == {"succeeded"}


def _write_voice_wav(path: Path, seconds: float, sample_rate: int = 16_000) -> None:
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
