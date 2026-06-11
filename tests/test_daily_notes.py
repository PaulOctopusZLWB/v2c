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
            '<!-- pcn:user end type="user_notes" -->',
            '用户保留的日报自由笔记。\n<!-- pcn:user end type="user_notes" -->',
        ),
        encoding="utf-8",
    )

    publish_daily_note(config=config, day="2087-05-10")

    republished = note_path.read_text(encoding="utf-8")
    assert "用户保留的日报自由笔记。" in republished
    assert republished.count('<!-- pcn:user start type="user_notes" -->') == 1
    assert republished.count('<!-- pcn:user end type="user_notes" -->') == 1


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


def _insert_daily_summary(database_path: Path) -> None:
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
