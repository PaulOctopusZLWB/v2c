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
    # Per §29.7 the transcript is not embedded in the note; it is queried on demand.
    assert "## Transcript" not in session_text
    assert "run-all 转写文本" not in session_text
    session_id = session_notes[0].stem
    transcript_result = CliRunner().invoke(
        app,
        ["session-transcript", "--session-id", session_id, "--config", str(config_path)],
    )
    assert transcript_result.exit_code == 0, transcript_result.output
    assert "## Transcript" in transcript_result.output
    assert "run-all 转写文本" in transcript_result.output
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


def test_run_all_cli_continues_past_a_task_failure(tmp_path: Path, monkeypatch) -> None:
    # §36: a single task failure is isolated and retryable — it must NOT abort the whole
    # run-all drain. The loop catches the exception, counts tasks_failed, and keeps draining.
    from personal_context_node import process_runner
    from personal_context_node.process_runner import ProcessOnceResult

    source_dir = tmp_path / "empty_source"
    source_dir.mkdir()
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[vad]
backend = "energy"

[llm]
backend = "rule_based"
""".strip(),
        encoding="utf-8",
    )

    calls = {"n": 0}

    def fake_process_once(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient task failure")
        return ProcessOnceResult(task_id="t", task_type="asr", status="no_task")

    # The drain loop now lives in process_runner.drain_process_queue (shared by CLI and web),
    # so patch process_once where that loop resolves it.
    monkeypatch.setattr(process_runner, "process_once", fake_process_once)

    result = CliRunner().invoke(
        app, ["run-all", "--config", str(config_path), "--mock", "--max-steps", "20"]
    )

    assert result.exit_code == 0, result.output  # did not abort on the failure
    assert "tasks_failed=1" in result.output
    assert "status=complete" in result.output
    assert calls["n"] == 2  # kept draining after the first failure
