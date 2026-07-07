from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.identity_review import (
    identity_review_for_session,
    record_not_person,
    safe_llm_segments,
    set_session_participant,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_initialize_creates_identity_sidecar_tables(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        tables = {row["name"] for row in fetch_all(conn, "select name from sqlite_master where type='table'")}
    finally:
        conn.close()

    assert "session_participants" in tables
    assert "segment_identity_negative_feedback" in tables


def test_identity_review_excludes_absent_person_without_rewriting_old_override(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_identity_session(config.database_path)
    set_session_participant(config=config, session_id="ses_1", person_id="per_a", status="present")
    set_session_participant(config=config, session_id="ses_1", person_id="per_c", status="absent")

    review = identity_review_for_session(config=config, session_id="ses_1")

    carol = next(c for c in review["candidates"] if c["person_id"] == "per_c")
    assert carol["status"] == "excluded"
    assert carol["safe_label"] == "未确认说话人_1"
    assert review["can_summarize"] is True
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select person_id, person_label from segment_person_overrides where segment_id='seg_2'")
    finally:
        conn.close()
    assert rows == [{"person_id": "per_c", "person_label": "Carol"}]


def test_negative_feedback_turns_known_person_into_unknown_for_safe_llm_context(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", send_speaker_labels=True)
    _seed_identity_session(config.database_path)
    set_session_participant(config=config, session_id="ses_1", person_id="per_a", status="present")
    set_session_participant(config=config, session_id="ses_1", person_id="per_c", status="present")
    record_not_person(config=config, session_id="ses_1", segment_ids=["seg_1"], person_id="per_a")

    segments, prompt_suffix = safe_llm_segments(
        config=config,
        session_id="ses_1",
        segments=_raw_summary_rows(config.database_path),
        include_speaker=True,
    )

    assert segments[0]["speaker"] == "未确认说话人_1"
    assert segments[1]["speaker"] == "Carol"
    assert "本场确认出现的人物: Alice, Carol" in prompt_suffix


def _seed_identity_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_a', 'Alice', 'contact', 0, 'now', 'now')")
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_c', 'Carol', 'contact', 0, 'now', 'now')")
        conn.execute("insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('aud_1', 'dev', '/tmp/a.wav', '/tmp/a.wav', 'sha', 2000, '2087-05-10T08:00:00+08:00', 'now', 'imported')")
        conn.execute("insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values ('ses_1', '2087-05-10', '2087-05-10T08:00:00+08:00', '2087-05-10T08:01:00+08:00', 'derived', 2, 2000, 'seg_1', 'now', 'now')")
        for segment_id, speaker, text, person_id, person_label in [
            ('seg_1', 'spk_01', 'hello', 'per_a', 'Alice'),
            ('seg_2', 'spk_02', 'world', 'per_c', 'Carol'),
        ]:
            conn.execute("insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active) values (?, 'aud_1', ?, 'ses_1', 0, 1000, ?, 'zh', ?, ?, ?, 1)", (segment_id, f'chk_{segment_id}', text, speaker, speaker, f'ev_{segment_id}'))
            conn.execute("insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, ?, 'now', ?, 'manual')", (segment_id, person_label, person_id))
        conn.commit()
    finally:
        conn.close()


def _raw_summary_rows(database_path: Path) -> list[dict[str, object]]:
    conn = connect(database_path)
    try:
        return fetch_all(conn, """
            select ts.segment_id, ts.speaker, ts.start_ms, ts.end_ms, ts.text, ts.evidence_id,
                   o.person_id, o.person_label
            from transcript_segments ts
            left join segment_person_overrides o on o.segment_id = ts.segment_id
            where ts.session_id='ses_1'
            order by ts.start_ms, ts.segment_id
        """)
    finally:
        conn.close()
