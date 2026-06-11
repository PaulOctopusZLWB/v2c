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
    assert (vault / "10_Daily" / "2087-05-10.md").exists()
