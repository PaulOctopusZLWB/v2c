import json
import pytest
from personal_context_node.config import AppConfig
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.session_merge import merge_sessions
from personal_context_node.storage.sqlite import connect
from test_sessions import _insert_audio_and_segments, _session_rows


def test_explicit_merge_preserves_labels_attendance_and_survives_rederive(tmp_path):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)
    derive_sessions_for_day(config=config, day="2087-05-10")
    ids = [r["session_id"] for r in _session_rows(config.database_path)]
    with connect(config.database_path) as conn:
        conn.execute("insert into persons(person_id,display_name,person_type,created_at,updated_at) values('p','王','contact','','')")
        for sid in ids:
            conn.execute("insert into session_participants values(?, 'p','present','manual','confirmed','')", (sid,))
    result = merge_sessions(config=config, session_ids=ids, reason="User confirmed one continuous meeting")
    assert result["segment_count"] == 2
    derive_sessions_for_day(config=config, day="2087-05-10")
    assert len(_session_rows(config.database_path)) == 1
    with connect(config.database_path) as conn:
        assert conn.execute("select count(*) from session_participants").fetchone()[0] == 1
        receipt = json.loads(conn.execute("select payload_json from session_merge_history").fetchone()[0])
        assert len(receipt["assignments_before"]) == 2
        assert len(receipt["participants_before"]) == 2
        assert not conn.execute("pragma foreign_key_check").fetchall()


def test_merge_rejects_conflicting_attendance_without_changing_sources(tmp_path):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)
    derive_sessions_for_day(config=config, day="2087-05-10")
    ids = [r["session_id"] for r in _session_rows(config.database_path)]
    with connect(config.database_path) as conn:
        conn.execute("insert into persons(person_id,display_name,person_type,created_at,updated_at) values('p','王','contact','','')")
        for sid, status in zip(ids, ("present", "absent")):
            conn.execute("insert into session_participants values(?, 'p',?,'manual','','')", (sid, status))
    with pytest.raises(ValueError, match="conflicting attendance"):
        merge_sessions(config=config, session_ids=ids, reason="merge")
    assert len(_session_rows(config.database_path)) == 2
