from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.config import AppConfig
from personal_context_node.session_summaries import summarize_session
from personal_context_node.session_viewpoint import (
    DEFAULT_SESSION_PROMPT,
    clear_viewpoint_edit,
    effective_session_prompt,
    get_session_prompt_template,
    session_fingerprint,
    set_segment_text,
    set_session_prompt_template,
    set_viewpoint_edit,
    viewpoint_state,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", 2, 2000, "seg_1", "2087-05-10T08:00:02+08:00", "2087-05-10T08:00:02+08:00"),
        )
        for index, (segment_id, text) in enumerate([("seg_1", "你好"), ("seg_2", "再见")]):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    segment_id, "aud_test", f"chk_{segment_id}", "ses_test",
                    index * 1000, (index + 1) * 1000,
                    f"2087-05-10T08:00:0{index}+08:00", f"2087-05-10T08:00:0{index + 1}+08:00",
                    text, "zh", "self", "self", f"ev_{index + 1}", 1.0,
                    "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:02+08:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _generated_content() -> dict[str, object]:
    return {
        "schema_version": "session_summary.v1",
        "session_id": "ses_test",
        "headline": "一个标题",
        "summary": "一段摘要。",
        "topics": [],
        "decisions": [],
        "todos": [],
        "open_questions": [],
        "core_conclusions": ["核心结论"],
        "per_speaker": [],
    }


def _insert_summary(database_path: Path, content: dict[str, object]) -> None:
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
                "sum_test", "session", "session", "ses_test",
                "llm_port.session_summary.v1", "mock", json.dumps(content),
                "2087-05-10T09:00:00+08:00", "2087-05-10T09:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_sidecar(database_path: Path, **columns: object) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        columns = {"session_id": "ses_test", "updated_at": "2087-05-10T10:00:00+08:00", **columns}
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"insert into session_viewpoint_state ({names}) values ({placeholders})",
            tuple(columns.values()),
        )
        conn.commit()
    finally:
        conn.close()


# --- session_fingerprint -------------------------------------------------


def test_session_fingerprint_is_stable() -> None:
    segments = [
        {"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None},
        {"segment_id": "s2", "text": "b", "speaker": "spk_1", "person_label": "Alice"},
    ]
    assert session_fingerprint(segments) == session_fingerprint(segments)


def test_session_fingerprint_is_order_sensitive() -> None:
    a = {"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None}
    b = {"segment_id": "s2", "text": "b", "speaker": "self", "person_label": None}
    assert session_fingerprint([a, b]) != session_fingerprint([b, a])


def test_session_fingerprint_changes_when_text_changes() -> None:
    before = [{"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None}]
    after = [{"segment_id": "s1", "text": "A", "speaker": "self", "person_label": None}]
    assert session_fingerprint(before) != session_fingerprint(after)


def test_session_fingerprint_changes_when_speaker_changes() -> None:
    before = [{"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None}]
    # A speaker correction surfaces via person_label, which should mark the result stale.
    after = [{"segment_id": "s1", "text": "a", "speaker": "self", "person_label": "Alice"}]
    assert session_fingerprint(before) != session_fingerprint(after)


# --- viewpoint_state -----------------------------------------------------


def test_viewpoint_state_no_summary(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["session_id"] == "ses_test"
    assert state["has_generated"] is False
    assert state["generated"] is None
    assert state["effective"] is None
    assert state["edited"] is None
    assert state["stale"] is False
    assert state["status"] == "draft"
    assert state["published_at"] is None
    assert state["note_path"] is None
    assert [s["segment_id"] for s in state["segments"]] == ["seg_1", "seg_2"]
    assert state["segments"][0]["text"] == "你好"
    assert state["segments"][0]["speaker"] == "self"
    assert "person_label" in state["segments"][0]


def test_viewpoint_state_with_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["has_generated"] is True
    assert state["generated"] == _generated_content()
    assert state["effective"] == _generated_content()
    assert state["edited"] is None


def test_viewpoint_state_edited_overrides_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    edited = {**_generated_content(), "headline": "手动改过的标题"}
    _insert_sidecar(config.database_path, edited_content_json=json.dumps(edited), status="edited")

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["edited"] == edited
    assert state["effective"] == edited
    assert state["generated"] == _generated_content()
    assert state["status"] == "edited"


def test_viewpoint_state_stale_when_fingerprint_mismatches(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    _insert_sidecar(config.database_path, source_fingerprint="stale-does-not-match")

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["stale"] is True


def test_viewpoint_state_not_stale_when_fingerprint_matches(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    # Compute the live fingerprint from the seeded segments and stash it as the stored one.
    live = viewpoint_state(config=config, session_id="ses_test")
    fingerprint = session_fingerprint(live["segments"])
    _insert_sidecar(config.database_path, source_fingerprint=fingerprint)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["stale"] is False


def test_viewpoint_state_not_stale_without_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    # A stored fingerprint but no generated summary -> never stale.
    _insert_sidecar(config.database_path, source_fingerprint="anything")

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["has_generated"] is False
    assert state["stale"] is False


# --- set_segment_text ----------------------------------------------------


def test_set_segment_text_updates_and_returns_true(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    assert set_segment_text(config=config, segment_id="seg_1", text="  改好的文字  ") is True

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select text from transcript_segments where segment_id = ?", ("seg_1",))
    finally:
        conn.close()
    assert rows[0]["text"] == "改好的文字"


def test_set_segment_text_unknown_id_returns_false(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    assert set_segment_text(config=config, segment_id="seg_missing", text="x") is False


# --- prompt template store (global) --------------------------------------


def test_get_session_prompt_template_defaults_to_constant(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    assert get_session_prompt_template(config=config) == DEFAULT_SESSION_PROMPT


def test_set_session_prompt_template_persists_global_override(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    set_session_prompt_template(config=config, template="你是定制助手。")

    assert get_session_prompt_template(config=config) == "你是定制助手。"


def test_set_session_prompt_template_none_resets_to_default(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    set_session_prompt_template(config=config, template="你是定制助手。")

    set_session_prompt_template(config=config, template=None)

    assert get_session_prompt_template(config=config) == DEFAULT_SESSION_PROMPT


def test_set_session_prompt_template_empty_resets_to_default(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    set_session_prompt_template(config=config, template="你是定制助手。")

    set_session_prompt_template(config=config, template="   ")

    assert get_session_prompt_template(config=config) == DEFAULT_SESSION_PROMPT


# --- effective_session_prompt (per-session > global > default) -----------


def test_effective_session_prompt_falls_back_to_default(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    prompt = effective_session_prompt(config=config, session_id="ses_test")

    assert prompt == {
        "effective": DEFAULT_SESSION_PROMPT,
        "default": DEFAULT_SESSION_PROMPT,
        "is_override": False,
    }


def test_effective_session_prompt_uses_global_template(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    set_session_prompt_template(config=config, template="全局模板。")

    prompt = effective_session_prompt(config=config, session_id="ses_test")

    assert prompt["effective"] == "全局模板。"
    assert prompt["default"] == "全局模板。"
    assert prompt["is_override"] is False


def test_effective_session_prompt_per_session_override_wins(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    set_session_prompt_template(config=config, template="全局模板。")
    _insert_sidecar(config.database_path, prompt_override="本会话专属模板。")

    prompt = effective_session_prompt(config=config, session_id="ses_test")

    assert prompt["effective"] == "本会话专属模板。"
    # default reports the GLOBAL template so the UI can offer "reset to global".
    assert prompt["default"] == "全局模板。"
    assert prompt["is_override"] is True


# --- summarize_session records fingerprint + clears edits -----------------


def test_summarize_session_writes_source_fingerprint(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    summarize_session(config=config, session_id="ses_test", llm=RuleBasedLLMAdapter())

    state = viewpoint_state(config=config, session_id="ses_test")
    expected = session_fingerprint(state["segments"])
    sidecar = _read_sidecar(config.database_path)
    assert sidecar is not None
    assert sidecar["source_fingerprint"] == expected
    # a fresh fingerprint == the live segments -> not stale.
    assert state["stale"] is False
    assert state["has_generated"] is True


def test_summarize_session_clears_prior_edits_and_keeps_prompt_override(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    edited = {**_generated_content(), "headline": "手动改过的标题"}
    _insert_sidecar(
        config.database_path,
        edited_content_json=json.dumps(edited),
        prompt_override="本会话专属模板。",
        status="edited",
    )

    summarize_session(config=config, session_id="ses_test", llm=RuleBasedLLMAdapter())

    sidecar = _read_sidecar(config.database_path)
    assert sidecar is not None
    # regenerate DISCARDS the prior manual edit + resets status to draft (by design).
    assert sidecar["edited_content_json"] is None
    assert sidecar["status"] == "draft"
    # but the per-session prompt override survives.
    assert sidecar["prompt_override"] == "本会话专属模板。"


def test_summarize_session_passes_effective_prompt_to_llm(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    set_session_prompt_template(config=config, template="全局模板。")
    _insert_sidecar(config.database_path, prompt_override="本会话专属模板。")

    captured: dict[str, object] = {}

    class RecordingLLM(RuleBasedLLMAdapter):
        def generate_session_summary(self, *, session_id, transcript_segments, prompt=None):
            captured["prompt"] = prompt
            return super().generate_session_summary(
                session_id=session_id, transcript_segments=transcript_segments
            )

    summarize_session(config=config, session_id="ses_test", llm=RecordingLLM())

    assert str(captured["prompt"]).startswith("本会话专属模板。")
    assert "本场确认出现的人物" in str(captured["prompt"])
    assert "不得输出任何未确认人物姓名" in str(captured["prompt"])


# --- viewpoint_state surfaces prompt block + generating -------------------


def _enqueue_summarize_task(config: AppConfig) -> None:
    from personal_context_node.tasks import enqueue_task

    enqueue_task(
        config=config,
        task_type="summarize_session",
        target_type="session",
        target_id="ses_test",
        priority=10,
    )


def test_viewpoint_state_includes_prompt_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["prompt"] == {
        "effective": DEFAULT_SESSION_PROMPT,
        "default": DEFAULT_SESSION_PROMPT,
        "is_override": False,
    }


def test_viewpoint_state_generating_false_without_task(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["generating"] is False


def test_viewpoint_state_generating_true_with_pending_task(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _enqueue_summarize_task(config)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["generating"] is True


# --- set_viewpoint_edit / clear_viewpoint_edit ---------------------------


def test_set_viewpoint_edit_validates_stores_and_sets_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    edited = {**_generated_content(), "headline": "手动改过的标题", "summary": "手动改过的摘要。"}

    set_viewpoint_edit(config=config, session_id="ses_test", content=edited)

    state = viewpoint_state(config=config, session_id="ses_test")
    assert state["edited"] == edited
    assert state["effective"] == edited
    assert state["generated"] == _generated_content()
    assert state["status"] == "edited"


def test_set_viewpoint_edit_rejects_invalid_doc(tmp_path: Path) -> None:
    import pytest

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    bad = {"headline": "缺少必填字段"}  # missing session_id/summary etc. -> validation raises

    with pytest.raises(Exception):
        set_viewpoint_edit(config=config, session_id="ses_test", content=bad)

    # nothing was stored: status stays draft, no edited content.
    state = viewpoint_state(config=config, session_id="ses_test")
    assert state["status"] == "draft"
    assert state["edited"] is None


def test_clear_viewpoint_edit_reverts_to_generated_baseline(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    edited = {**_generated_content(), "headline": "手动改过的标题"}
    set_viewpoint_edit(config=config, session_id="ses_test", content=edited)

    clear_viewpoint_edit(config=config, session_id="ses_test")

    state = viewpoint_state(config=config, session_id="ses_test")
    assert state["edited"] is None
    assert state["effective"] == _generated_content()
    assert state["status"] == "draft"


# --- publish_session_viewpoint (one-way, from effective content) ----------


def test_publish_session_viewpoint_writes_note_and_records_sidecar(tmp_path: Path) -> None:
    from personal_context_node.obsidian_sessions import publish_session_viewpoint

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())

    result = publish_session_viewpoint(config=config, session_id="ses_test")

    note_path = Path(result["note_path"])
    assert note_path.exists()
    # the standard per-day session note path.
    assert note_path == config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md"
    text = note_path.read_text(encoding="utf-8")
    assert "一个标题" in text  # generated headline rendered

    state = viewpoint_state(config=config, session_id="ses_test")
    assert state["status"] == "published"
    assert state["note_path"] == str(note_path)
    assert state["published_at"] is not None


def test_publish_session_viewpoint_renders_edited_content(tmp_path: Path) -> None:
    from personal_context_node.obsidian_sessions import publish_session_viewpoint

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    edited = {
        **_generated_content(),
        "headline": "手动改过的标题",
        "summary": "这是手动编辑后的摘要内容。",
    }
    set_viewpoint_edit(config=config, session_id="ses_test", content=edited)

    result = publish_session_viewpoint(config=config, session_id="ses_test")

    text = Path(result["note_path"]).read_text(encoding="utf-8")
    # the EDITED text is published, NOT the generated baseline.
    assert "手动改过的标题" in text
    assert "这是手动编辑后的摘要内容。" in text
    assert "一个标题" not in text


def test_publish_session_viewpoint_errors_without_effective(tmp_path: Path) -> None:
    import pytest

    from personal_context_node.obsidian_sessions import publish_session_viewpoint

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    # nothing generated, nothing edited -> no effective content to publish.

    with pytest.raises(Exception):
        publish_session_viewpoint(config=config, session_id="ses_test")


def _read_sidecar(database_path: Path) -> dict[str, object] | None:
    conn = connect(database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select * from session_viewpoint_state where session_id = ?",
            ("ses_test",),
        )
    finally:
        conn.close()
    return rows[0] if rows else None
