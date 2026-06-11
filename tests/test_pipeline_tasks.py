from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.tasks import process_status_rows


def test_import_enqueues_vad_tasks_for_new_audio(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    tasks = process_status_rows(config=config)
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "vad"
    assert tasks[0]["target_type"] == "audio_file"
    assert tasks[0]["status"] == "pending"


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
