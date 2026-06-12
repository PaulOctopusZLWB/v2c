from __future__ import annotations

import math
import re
import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all


def test_first_slice_cli_chain_reaches_verified_signed_memory(tmp_path: Path) -> None:
    source_dir = tmp_path / "DJI_MIC"
    _write_voice_wav(source_dir / "TX02_MIC001_20870510_173550_orig.wav")
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    runner = CliRunner()

    init_result = runner.invoke(
        app,
        ["init", "--data-dir", str(data_dir), "--obsidian-vault", str(vault), "--config-path", str(config_path)],
    )
    assert init_result.exit_code == 0, init_result.output
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[device.dji_mic_3]
root_path = "{source_dir}"
volume_name_patterns = ["*"]
stable_seconds = 0

[vad]
backend = "energy"
threshold = 0.05
min_speech_ms = 100
merge_gap_ms = 100
max_chunk_ms = 1000
chunk_overlap_ms = 0

[asr]
backend = "mock"

[llm]
backend = "rule_based"

[obsidian]
edit_grace_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    health_result = runner.invoke(app, ["health", "--config", str(config_path)])
    assert health_result.exit_code == 0, health_result.output
    assert "status=ok" in health_result.output

    ingest_result = runner.invoke(app, ["ingest", "import", "--config", str(config_path)])
    assert ingest_result.exit_code == 0, ingest_result.output
    assert "imported_files=1" in ingest_result.output

    process_outputs = []
    for _ in range(8):
        result = runner.invoke(app, ["process", "run", "--mock", "--config", str(config_path)])
        assert result.exit_code == 0, result.output
        process_outputs.append(result.output)
        if "status=no_task" in result.output:
            break

    assert any("task_type=vad" in output for output in process_outputs)
    assert any("task_type=asr" in output for output in process_outputs)
    assert any("task_type=daily_generate" in output for output in process_outputs)

    publish_result = runner.invoke(app, ["obsidian", "publish", "--config", str(config_path), "--date", "2025-06-10"])
    assert publish_result.exit_code == 0, publish_result.output
    assert "candidate_review_written=1" in publish_result.output

    review_path = vault / "30_Memory_Candidates" / "2025-06-10.md"
    review_text = review_path.read_text(encoding="utf-8")
    review_text = re.sub(r"- \[ \] (cand_[^ ]+)", r"- [x] \1", review_text, count=1)
    review_text = review_text.replace("action: pending", "action: confirm", 1)
    review_path.write_text(review_text, encoding="utf-8")

    sync_result = runner.invoke(app, ["obsidian", "sync-review", "--config", str(config_path), "--date", "2025-06-10"])
    assert sync_result.exit_code == 0, sync_result.output
    assert "candidates_confirmed=1" in sync_result.output
    assert "signed_events_created=1" in sync_result.output

    verify_result = runner.invoke(app, ["memory", "verify", "--config", str(config_path)])
    assert verify_result.exit_code == 0, verify_result.output
    assert "invalid_events=0" in verify_result.output

    config = AppConfig.from_toml(config_path)
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select status from memory_cards")
        events = fetch_all(conn, "select trust_status from signed_events")
    finally:
        conn.close()
    assert cards == [{"status": "active"}]
    assert events == [{"trust_status": "trusted"}]


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
