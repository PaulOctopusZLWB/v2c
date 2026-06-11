from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_daily import publish_daily_note
from personal_context_node.storage.sqlite import connect, initialize


def test_publish_daily_note_preserves_user_notes_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_daily_summary(config.database_path)
    publish_daily_note(config=config, day="2087-05-10")
    note_path = config.obsidian_vault / "10_Daily" / "2087-05-10.md"
    text = note_path.read_text(encoding="utf-8")
    note_path.write_text(
        text.replace(
            '<!-- pcn:block end id="user_notes" -->',
            '用户保留的日报自由笔记。\n<!-- pcn:block end id="user_notes" -->',
        ),
        encoding="utf-8",
    )

    publish_daily_note(config=config, day="2087-05-10")

    republished = note_path.read_text(encoding="utf-8")
    assert "用户保留的日报自由笔记。" in republished
    assert republished.count('<!-- pcn:block start id="user_notes" kind="user" version="1" -->') == 1
    assert republished.count('<!-- pcn:block end id="user_notes" -->') == 1


def test_publish_daily_note_migrates_legacy_user_notes_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_daily_summary(config.database_path)
    publish_daily_note(config=config, day="2087-05-10")
    note_path = config.obsidian_vault / "10_Daily" / "2087-05-10.md"
    note_path.write_text(
        """
---
pcn_schema: markdown_note.v1
note_type: daily
date_key: 2087-05-10
---

## User Notes

<!-- pcn:user start type="user_notes" -->
旧格式自由笔记。
<!-- pcn:user end type="user_notes" -->
""".lstrip(),
        encoding="utf-8",
    )

    publish_daily_note(config=config, day="2087-05-10")

    republished = note_path.read_text(encoding="utf-8")
    assert "旧格式自由笔记。" in republished
    assert "pcn:user start" not in republished
    assert '<!-- pcn:block start id="user_notes" kind="user" version="1" -->' in republished


def test_publish_daily_note_writes_markdown_frontmatter(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_daily_summary(config.database_path)

    publish_daily_note(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "10_Daily" / "2087-05-10.md"
    text = note_path.read_text(encoding="utf-8")
    assert text.startswith("---\npcn_schema: markdown_note.v1\nnote_type: daily\ndate_key: 2087-05-10\n")
    assert "generated_by: personal-context-node\n" in text
    assert "generated_at: " in text
    assert "\npcn_managed: true\n---\n" in text


def test_publish_daily_note_uses_protocol_block_markers(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_daily_summary(config.database_path)

    publish_daily_note(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "10_Daily" / "2087-05-10.md"
    text = note_path.read_text(encoding="utf-8")
    assert '<!-- pcn:block start id="daily_headline" kind="managed" version="1" -->' in text
    assert '<!-- pcn:block end id="daily_headline" -->' in text
    assert '<!-- pcn:block start id="daily_metrics" kind="managed" version="1" -->' in text
    assert '<!-- pcn:block start id="user_notes" kind="user" version="1" -->' in text
    assert '<!-- pcn:block end id="user_notes" -->' in text
    assert "pcn:managed start" not in text
    assert "pcn:user start" not in text


def test_publish_daily_note_counts_metrics_by_session_date_key(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_daily_summary(config.database_path, recorded_at="2087-05-09T23:55:00+08:00")

    publish_daily_note(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "10_Daily" / "2087-05-10.md"
    text = note_path.read_text(encoding="utf-8")
    assert "- Total imported files: 1" in text
    assert "- Total duration ms: 1000" in text


def _insert_daily_summary(database_path: Path, *, recorded_at: str = "2087-05-10T00:00:00+08:00") -> None:
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
                recorded_at,
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into sessions (
              session_id, date_key, started_at, ended_at, source,
              segment_count, active_speech_ms, first_segment_id,
              exclude_from_memory, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_test",
                "2087-05-10",
                recorded_at,
                recorded_at,
                "derived_from_segments",
                1,
                1000,
                "seg_test",
                0,
                "2087-05-10T00:10:00+08:00",
                "2087-05-10T00:10:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
                "ses_test",
                0,
                1000,
                "今天验证了本地音频处理链路。",
                "zh",
                "self",
                "ev_test",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.execute(
            """
            insert into summaries (
              summary_id, summary_type, target_type, target_id, prompt_version,
              model_name, content_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sum_daily_test",
                "daily",
                "date_key",
                "2087-05-10",
                "llm_port.daily_summary.v1",
                "rule_based",
                json.dumps(
                    {
                        "schema_version": "daily_summary.v1",
                        "headline": "本地处理日报",
                        "summary": "今天验证了本地音频处理链路。",
                        "todos_rollup": [],
                        "decisions_rollup": [],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
