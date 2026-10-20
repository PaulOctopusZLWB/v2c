from personal_context_node.config import AppConfig
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.storage.sqlite import connect
from test_sessions import _insert_audio_and_segments, _session_rows


def test_rederive_keeps_optional_artifact_anchors_and_processes_new_segments(tmp_path):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_audio_and_segments(config.database_path)
    derive_sessions_for_day(config=config, day="2087-05-10")
    before = _session_rows(config.database_path)
    anchor = before[0]["session_id"]
    with connect(config.database_path) as conn:
        conn.execute("""create table optional_review (
            session_id text references sessions(session_id), payload text)""")
        conn.execute("insert into optional_review values (?, 'reviewed evidence')", (anchor,))
    result = derive_sessions_for_day(config=config, day="2087-05-10")
    assert result.sessions_derived == 1
    assert _session_rows(config.database_path) == before
    with connect(config.database_path) as conn:
        assert tuple(conn.execute("select * from optional_review").fetchone()) == (anchor, "reviewed evidence")
        assert not conn.execute("pragma foreign_key_check").fetchall()
