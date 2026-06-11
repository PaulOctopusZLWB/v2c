from __future__ import annotations

import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)


def test_run_first_milestone_cli_writes_daily_note(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "data"
    vault = tmp_path / "vault"

    result = CliRunner().invoke(
        app,
        [
            "run-first-milestone",
            "--source-dir",
            str(source),
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
            "--confirm-first-candidate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    assert (vault / "10_Daily" / "2025-06-10.md").exists()


def test_preprocess_cli_creates_audio_chunks(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    runner = CliRunner()
    import_result = runner.invoke(
        app,
        [
            "run-first-milestone",
            "--source-dir",
            str(source),
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
        ],
    )
    assert import_result.exit_code == 0, import_result.output

    preprocess_result = runner.invoke(
        app,
        [
            "preprocess",
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
            "--vad-threshold",
            "0.0001",
            "--max-chunk-ms",
            "300",
        ],
    )

    assert preprocess_result.exit_code == 0, preprocess_result.output
    assert "audio_files_processed=1" in preprocess_result.output
    assert "audio_chunks_created=" in preprocess_result.output
    assert list((data / "audio" / "work" / "2025-06-10").glob("*.wav"))


def test_preprocess_cli_uses_command_vad_backend(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    vad_script = tmp_path / "fake_vad.py"
    vad_script.write_text(
        """
import json
print(json.dumps({"ranges": [{"start_ms": 0, "end_ms": 500}]}))
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    assert runner.invoke(
        app,
        ["run-first-milestone", "--source-dir", str(source), "--data-dir", str(data), "--obsidian-vault", str(vault)],
    ).exit_code == 0

    preprocess_result = runner.invoke(
        app,
        [
            "preprocess",
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
            "--vad-backend",
            "command",
            "--vad-command",
            f"python3 {vad_script}",
            "--max-chunk-ms",
            "300",
        ],
    )

    assert preprocess_result.exit_code == 0, preprocess_result.output
    assert "audio_chunks_created=2" in preprocess_result.output


def test_transcribe_cli_processes_pending_chunks(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    runner = CliRunner()
    assert runner.invoke(
        app,
        ["run-first-milestone", "--source-dir", str(source), "--data-dir", str(data), "--obsidian-vault", str(vault)],
    ).exit_code == 0
    assert runner.invoke(
        app,
        [
            "preprocess",
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
            "--vad-threshold",
            "0.0001",
            "--max-chunk-ms",
            "300",
        ],
    ).exit_code == 0

    result = runner.invoke(
        app,
        [
            "transcribe",
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
            "--mock-text",
            "CLI 本地转写",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "chunks_transcribed=" in result.output
    assert "segments_created=" in result.output
