from __future__ import annotations

import ast
import wave
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import PIPELINE
from personal_context_node.tasks import process_status_rows


def test_import_enqueues_vad_tasks_for_new_audio(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    tasks = process_status_rows(config=config)
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "vad"
    assert tasks[0]["target_type"] == "audio_file"
    assert tasks[0]["status"] == "pending"


def test_pipeline_declares_all_task_edges() -> None:
    edges = [
        (edge.upstream_task_type, edge.downstream_task_type, edge.downstream_target_type)
        for edge in PIPELINE
    ]

    assert edges == [
        ("vad", "asr", "audio_chunk"),
        ("asr", "session_derive", "date_key"),
        # Diarize-mode sibling: the whole-FILE transcribe_diarize stage fans into session_derive
        # (round-7 invariant per audio_file). Only the active mode's tasks exist at runtime.
        ("transcribe_diarize", "session_derive", "date_key"),
        ("session_derive", "summarize_session", "session"),
        ("summarize_session", "daily_generate", "date_key"),
        ("daily_generate", "obsidian_publish", "date_key"),
        # Feature-extraction LEAF (both ASR modes): fans in from transcription, gates nothing.
        ("transcribe_diarize", "extract_features", "audio_file"),
        ("asr", "extract_features", "audio_file"),
        # Speaker-identification LEAF: dual upstream covers both orderings of the
        # extraction/derivation race; gates nothing downstream.
        ("extract_features", "identify_speakers", "session"),
        ("session_derive", "identify_speakers", "session"),
    ]


def test_process_once_uses_pipeline_for_downstream_task_registration() -> None:
    module_path = Path("src/personal_context_node/process_runner.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    process_once = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "process_once")

    direct_enqueue_calls = [
        node
        for node in ast.walk(process_once)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "enqueue_task"
    ]

    assert direct_enqueue_calls == []


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
