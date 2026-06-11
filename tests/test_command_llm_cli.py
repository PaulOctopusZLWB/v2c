from __future__ import annotations

import stat
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.storage.sqlite import connect, initialize


def test_summarize_cli_uses_command_llm_backend(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    _insert_transcript(data_dir / "db" / "personal_context.sqlite")
    script = tmp_path / "fake_llm.py"
    script.write_text(
        """
import json
import sys
payload = json.loads(sys.stdin.read())
first = payload["transcript_segments"][0]["evidence_id"]
print(json.dumps({
  "summary": "命令式 LLM 摘要",
  "todos": [],
  "facts": [],
  "inferences": [],
  "memory_candidates": [{
    "candidate_claim": "命令式 LLM 候选",
    "claim_type": "observation",
    "confidence": 0.7,
    "evidence_refs": [first]
  }]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    result = CliRunner().invoke(
        app,
        [
            "summarize",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
            "--day",
            "2087-05-10",
            "--llm-backend",
            "command",
            "--llm-command",
            f"python3 {script}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "summaries_created=1" in result.output
    assert "memory_candidates_created=1" in result.output


def _insert_transcript(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
                0,
                1000,
                "我决定继续接入真实 ASR。",
                "zh",
                "self",
                "ev_test",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.commit()
    finally:
        conn.close()
