from __future__ import annotations

import math
import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.storage.sqlite import connect, fetch_all


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)


def _write_tone_wav(path: Path, *, seconds: float, amplitude: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(seconds * sample_rate)):
            sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))


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


def test_run_first_milestone_cli_uses_config_path(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(f"[paths]\ndata_dir = '{data}'\nobsidian_vault = '{vault}'\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-first-milestone",
            "--config",
            str(config_path),
            "--source-dir",
            str(source),
            "--confirm-first-candidate",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    assert (vault / "10_Daily" / "2025-06-10.md").exists()
    assert (data / "db" / "personal_context.sqlite").exists()


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


def test_preprocess_cli_uses_vad_settings_from_config(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tone_wav(source / "TX02_MIC001_20870510_173550_orig.wav", seconds=0.30, amplitude=10_000)
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data}"
obsidian_vault = "{vault}"

[vad]
backend = "energy"
threshold = 0.05
min_speech_ms = 500
merge_gap_ms = 100
max_chunk_ms = 1000
chunk_overlap_ms = 0
""".strip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    import_result = runner.invoke(
        app,
        [
            "ingest-import",
            "--source-dir",
            str(source),
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
        ],
    )
    assert import_result.exit_code == 0, import_result.output

    preprocess_result = runner.invoke(app, ["preprocess", "--config", str(config_path)])

    assert preprocess_result.exit_code == 0, preprocess_result.output
    assert "audio_files_processed=1" in preprocess_result.output
    assert "audio_chunks_created=0" in preprocess_result.output


def test_preprocess_cli_uses_chunk_overlap_from_config(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tone_wav(source / "TX02_MIC001_20870510_173550_orig.wav", seconds=0.90, amplitude=10_000)
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data}"
obsidian_vault = "{vault}"

[vad]
backend = "energy"
threshold = 0.05
min_speech_ms = 100
merge_gap_ms = 100
max_chunk_ms = 400
chunk_overlap_ms = 100
""".strip(),
        encoding="utf-8",
    )
    runner = CliRunner()
    import_result = runner.invoke(
        app,
        [
            "ingest-import",
            "--source-dir",
            str(source),
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
        ],
    )
    assert import_result.exit_code == 0, import_result.output

    preprocess_result = runner.invoke(app, ["preprocess", "--config", str(config_path)])

    assert preprocess_result.exit_code == 0, preprocess_result.output
    assert "audio_chunks_created=3" in preprocess_result.output


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


def test_transcribe_cli_uses_asr_settings_from_config(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data}"
obsidian_vault = "{vault}"

[asr]
backend = "mock"
language = "yue"
model_name = "configured-mock-asr"
""".strip(),
        encoding="utf-8",
    )
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

    result = runner.invoke(app, ["transcribe", "--config", str(config_path), "--mock-text", "配置 ASR 输出"])

    assert result.exit_code == 0, result.output
    conn = connect(data / "db" / "personal_context.sqlite")
    try:
        rows = fetch_all(conn, "select language, model_name, text from transcript_segments where is_active = 1 order by start_ms")
    finally:
        conn.close()
    assert rows
    assert all(row == {"language": "yue", "model_name": "configured-mock-asr", "text": "配置 ASR 输出"} for row in rows)
