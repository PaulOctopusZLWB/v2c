from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_sessions import publish_session_notes
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.summary_schemas import validate_session_summary


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
    # Per §29.7 the full transcript must NOT be embedded in the session note.
    assert "## Transcript" not in text
    assert 'id="session_transcript"' not in text


def test_publish_session_notes_omits_transcript_but_cli_renders_it(tmp_path: Path) -> None:
    from personal_context_node.obsidian_sessions import session_transcript_lines

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_transcript_segment(config.database_path)

    publish_session_notes(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")
    assert "我们继续开发 run-all。" not in text

    rendered = "\n".join(session_transcript_lines(config=config, session_id="ses_test"))
    assert "## Transcript" in rendered
    assert (
        "- `00:01.000-00:02.500` **self**: 我们继续开发 run-all。 "
        "_(tags: yue, EMO_UNKNOWN, Speech, withitn)_"
    ) in rendered


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


def _managed_block(text: str) -> str:
    start = '<!-- pcn:block start id="session_summary" kind="managed" version="1" -->'
    end = '<!-- pcn:block end id="session_summary" -->'
    return text.split(start, 1)[1].split(end, 1)[0]


def test_publish_session_notes_renders_per_speaker_analysis(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_session_summary(
        config.database_path,
        core_conclusions=["团队同意推进本地化部署。", "下一步聚焦说话人聚类。"],
        per_speaker=[
            {
                "speaker_cluster_id": "spk_01",
                "viewpoints": [
                    {"text": "应优先保证音频本地处理。", "evidence_refs": ["ev_1"]},
                    {"text": "faster-whisper 可作备选。", "evidence_refs": []},
                ],
                "sentiment": "积极",
                "stance": "支持本地化",
                "latent_needs": ["隐私保障", "可控的迭代节奏"],
            },
            {
                "speaker_cluster_id": "spk_02",
                "viewpoints": [
                    {"text": "担心算力成本过高。", "evidence_refs": ["ev_2"]},
                ],
                "sentiment": "谨慎",
                "stance": "需要评估成本",
                "latent_needs": ["成本可预测"],
            },
        ],
    )

    publish_session_notes(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")
    block = _managed_block(text)

    # 核心结论 section, inside the managed block.
    assert "核心结论" in block
    assert "团队同意推进本地化部署。" in block
    assert "下一步聚焦说话人聚类。" in block

    # Per-speaker section: speaker labels.
    assert "发言人 spk_01" in block
    assert "发言人 spk_02" in block

    # spk_01 details.
    assert "应优先保证音频本地处理。" in block
    assert "faster-whisper 可作备选。" in block
    assert "积极" in block
    assert "支持本地化" in block
    assert "隐私保障" in block
    assert "可控的迭代节奏" in block

    # spk_02 details.
    assert "担心算力成本过高。" in block
    assert "谨慎" in block
    assert "需要评估成本" in block
    assert "成本可预测" in block


def test_publish_session_notes_per_speaker_is_idempotent(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_session_summary(
        config.database_path,
        core_conclusions=["团队同意推进本地化部署。"],
        per_speaker=[
            {
                "speaker_cluster_id": "spk_01",
                "viewpoints": [{"text": "应优先保证音频本地处理。", "evidence_refs": ["ev_1"]}],
                "sentiment": "积极",
                "stance": "支持本地化",
                "latent_needs": ["隐私保障"],
            },
        ],
    )

    publish_session_notes(config=config, day="2087-05-10")
    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    first = note_path.read_text(encoding="utf-8")

    publish_session_notes(config=config, day="2087-05-10")
    second = note_path.read_text(encoding="utf-8")

    assert second.count("核心结论") == first.count("核心结论") == 1
    assert second.count("发言人 spk_01") == first.count("发言人 spk_01") == 1
    assert second.count('<!-- pcn:block start id="session_summary" kind="managed" version="1" -->') == 1


def test_publish_session_notes_omits_per_speaker_when_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_session_summary(
        config.database_path,
        core_conclusions=[],
        per_speaker=[],
    )

    publish_session_notes(config=config, day="2087-05-10")

    note_path = config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")

    # Non-diarized path renders no new per-speaker / 核心结论 content.
    assert "核心结论" not in text
    assert "发言人" not in text
    # Existing session note still renders as before.
    assert "# 本地化部署讨论" in text
    assert "## 本地化部署讨论" in text
    assert "三段以内摘要。" in text
    assert '<!-- pcn:block start id="session_summary" kind="managed" version="1" -->' in text
    assert '<!-- pcn:block end id="session_summary" -->' in text


def _insert_session_summary(
    database_path: Path,
    *,
    core_conclusions: list[str],
    per_speaker: list[dict[str, object]],
) -> None:
    content = validate_session_summary(
        {
            "schema_version": "session_summary.v1",
            "session_id": "ses_test",
            "headline": "本地化部署讨论",
            "summary": "三段以内摘要。",
            "topics": [],
            "decisions": [],
            "todos": [],
            "open_questions": [],
            "core_conclusions": core_conclusions,
            "per_speaker": per_speaker,
        }
    )
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into summaries (
              summary_id, summary_type, target_type, target_id, prompt_version,
              model_name, content_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sum_test",
                "session",
                "session",
                "ses_test",
                "llm_port.session_summary.v1",
                "rule_based",
                json.dumps(content, ensure_ascii=False),
                "2087-05-10T09:30:00+08:00",
                "2087-05-10T09:30:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
