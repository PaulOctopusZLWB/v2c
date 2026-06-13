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
    assert text.startswith(
        "---\npcn_schema: markdown_note.v1\nnote_type: session\ndate_key: 2087-05-10\nsession_id: ses_test\n"
    )
    assert "generated_by: personal-context-node\n" in text
    assert "generated_at: " in text
    assert "\npcn_managed: true\n---\n" in text
    assert "# Session ses_test" in text
    assert '<!-- pcn:block start id="session_summary" kind="managed" version="1" -->' in text
    assert '<!-- pcn:block end id="session_summary" -->' in text
    assert "segment_count: 2" in text
    assert "## Transcript" in text
    assert "暂无转写片段。" in text
    assert "完整转写不进入 session note" not in text


def test_publish_session_notes_includes_transcript_text_and_asr_tags(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_transcript_segment(config.database_path)

    publish_session_notes(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")
    assert "## Transcript" in text
    assert (
        "- `00:01.000-00:02.500` **self**: 我们继续开发 run-all。 "
        "_(tags: yue, EMO_UNKNOWN, Speech, withitn)_"
    ) in text


def test_publish_session_notes_preserves_user_notes_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    publish_session_notes(config=config, day="2087-05-10")
    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")
    note_path.write_text(
        text.replace(
            '<!-- pcn:block end id="user_notes" -->',
            '用户保留的自由笔记。\n<!-- pcn:block end id="user_notes" -->',
        ),
        encoding="utf-8",
    )

    publish_session_notes(config=config, day="2087-05-10")

    republished = note_path.read_text(encoding="utf-8")
    assert "用户保留的自由笔记。" in republished
    assert republished.count('<!-- pcn:block start id="user_notes" kind="user" version="1" -->') == 1
    assert republished.count('<!-- pcn:block end id="user_notes" -->') == 1


def test_publish_session_notes_migrates_legacy_user_notes_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    publish_session_notes(config=config, day="2087-05-10")
    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    note_path.write_text(
        """
---
pcn_schema: markdown_note.v1
note_type: session
date_key: 2087-05-10
session_id: ses_test
---

## User Notes

<!-- pcn:user start type="user_notes" -->
旧格式 session 自由笔记。
<!-- pcn:user end type="user_notes" -->
""".lstrip(),
        encoding="utf-8",
    )

    publish_session_notes(config=config, day="2087-05-10")

    republished = note_path.read_text(encoding="utf-8")
    assert "旧格式 session 自由笔记。" in republished
    assert "pcn:user start" not in republished
    assert '<!-- pcn:block start id="user_notes" kind="user" version="1" -->' in republished


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


def _insert_transcript_segment(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns,
              local_raw_path, sha256, duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source/TX02_MIC001_20870510_080000_orig.wav",
                1024,
                1,
                "/data/audio/raw/TX02_MIC001_20870510_080000_orig.wav",
                "sha256:test",
                10000,
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:00:01+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms,
              absolute_start_at, absolute_end_at, text, language, speaker,
              speaker_cluster_id, evidence_id, confidence, asr_backend,
              model_name, model_version, asr_tags_json, is_active, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
                "ses_test",
                1000,
                2500,
                "2087-05-10T08:00:01+08:00",
                "2087-05-10T08:00:02.500000+08:00",
                "我们继续开发 run-all。",
                "zh",
                "self",
                "self",
                "ev_seg_test",
                0.91,
                "CommandASRAdapter",
                "sensevoice",
                "funasr-sensevoice-local",
                '["yue", "EMO_UNKNOWN", "Speech", "withitn"]',
                1,
                "2087-05-10T08:00:03+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
