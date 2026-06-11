from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_sessions import publish_session_notes
from personal_context_node.storage.sqlite import connect, initialize


def test_publish_session_notes_creates_stable_session_note(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    result = publish_session_notes(config=config, day="2087-05-10")

    assert result.notes_written == 1
    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    assert note_path.exists()
    text = note_path.read_text(encoding="utf-8")
    assert "# Session ses_test" in text
    assert "<!-- pcn:managed start type=\"session_summary\"" in text
    assert "segment_count: 2" in text
    assert "完整转写不进入 session note" in text


def test_publish_session_notes_preserves_user_notes_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    publish_session_notes(config=config, day="2087-05-10")
    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")
    note_path.write_text(
        text.replace(
            "<!-- pcn:user end type=\"user_notes\" -->",
            "用户保留的自由笔记。\n<!-- pcn:user end type=\"user_notes\" -->",
        ),
        encoding="utf-8",
    )

    publish_session_notes(config=config, day="2087-05-10")

    republished = note_path.read_text(encoding="utf-8")
    assert "用户保留的自由笔记。" in republished
    assert republished.count("<!-- pcn:user start type=\"user_notes\" -->") == 1
    assert republished.count("<!-- pcn:user end type=\"user_notes\" -->") == 1


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into sessions (
              session_id, date_key, started_at, ended_at, source,
              segment_count, active_speech_ms, first_segment_id, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_test",
                "2087-05-10",
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:10:00+08:00",
                "derived_from_segments",
                2,
                120000,
                "seg_1",
                "2087-05-10T09:00:00+08:00",
                "2087-05-10T09:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
