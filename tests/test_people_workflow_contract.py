import pytest
from fastapi.testclient import TestClient
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect
from personal_context_node.web.app import create_app
from test_web_identity_api import _seed_api_identity_session


@pytest.mark.parametrize("kind", ["contact", "self", "non_speaker", "unknown_voice"])
def test_manual_label_reconciles_only_real_person_attendance_in_selected_session(tmp_path, kind):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    with connect(config.database_path) as c:
        c.execute("update persons set person_type=? where person_id='per_a'", (kind,))
        c.execute("insert into sessions(session_id,date_key,started_at,ended_at,source,segment_count,active_speech_ms,first_segment_id,created_at,updated_at) values('other','2087-05-10','start','end','test',0,0,'other','','')")
        c.executemany("insert into session_participants values(?,'per_a','absent','manual',null,'')", [('ses_1',), ('other',)])
        c.execute("insert into segment_identity_negative_feedback values('seg_1','per_a','ses_1','manual',null,'')")
    client = TestClient(create_app(config=config))
    assert client.post("/api/people/per_a/label-segments", json={"segment_ids": ["seg_1", "seg_1"]}).json() == {"labeled": 1}
    with connect(config.database_path) as c:
        assert c.execute("select status from session_participants where session_id='ses_1'").fetchone()[0] == (
            "present" if kind in {"contact", "self"} else "absent")
        assert c.execute("select status from session_participants where session_id='other'").fetchone()[0] == "absent"
        assert c.execute("select count(*) from segment_identity_negative_feedback").fetchone()[0] == (
            0 if kind in {"contact", "self"} else 1)


@pytest.mark.parametrize("retired", [False, True])
def test_stale_selection_is_rejected_atomically(tmp_path, retired):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    with connect(config.database_path) as c:
        c.execute("delete from segment_person_overrides")
        if retired: c.execute("update transcript_segments set is_active=0 where segment_id='seg_1'")
    client = TestClient(create_app(config=config))
    response = client.post("/api/people/per_a/label-segments",
                           json={"segment_ids": ["seg_1"] if retired else ["seg_1", "missing"]})
    assert response.status_code == 404
    with connect(config.database_path) as c:
        assert c.execute("select count(*) from segment_person_overrides").fetchone()[0] == 0
