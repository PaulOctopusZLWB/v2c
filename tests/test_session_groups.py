from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.session_groups import session_groups
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def _seed(tmp_path: Path):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    initialize(conn)
    base = datetime.fromisoformat("2087-05-10T19:00:00+08:00")
    for n in range(3):
        start = base + timedelta(minutes=30 * n)
        end = start + timedelta(minutes=29, seconds=59)
        audio, session, segment = f"aud_{n}", f"ses_{n}", f"seg_{n}"
        conn.execute("""insert into audio_files (audio_file_id,source_device,source_path,local_raw_path,sha256,
            duration_ms,recorded_at,imported_at,status) values (?,?,?,?,?,?,?,?,?)""",
            (audio, "recorder", f"/source{n}", f"/local{n}", f"sha{n}", 1_800_000, start.isoformat(), "now", "imported"))
        conn.execute("""insert into sessions (session_id,date_key,started_at,ended_at,source,segment_count,
            active_speech_ms,first_segment_id,created_at,updated_at) values (?,?,?,?,?,?,?,?,?,?)""",
            (session, "2087-05-10", start.isoformat(), end.isoformat(), "derived", 1, 1000, segment, "now", "now"))
        conn.execute("""insert into transcript_segments (segment_id,audio_file_id,chunk_id,session_id,start_ms,end_ms,
            absolute_start_at,absolute_end_at,text,language,speaker,evidence_id,is_active)
            values (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (segment, audio, f"chunk_{n}", session, 0, 1799000, start.isoformat(), end.isoformat(), "test", "zh", "self", f"ev_{n}"))
    conn.commit()
    return config, conn


def _accepted(conn, *, partial: bool = False, stale: bool = False):
    conn.executescript("""
      create table if not exists logical_meetings (meeting_id text primary key,title text,state text,
        current_revision_id text,created_at text,updated_at text);
      create table if not exists logical_meeting_revisions (revision_id text primary key,meeting_id text,
        payload_json text,created_at text);
      create table if not exists session_evidence_heads (session_id text primary key,revision_id text,
        previous_revision_id text,updated_at text);
    """)
    spans = []
    has_evidence = conn.execute("select 1 from sqlite_master where type='table' and name='evidence_revisions'").fetchone()
    for n in range(3):
        if has_evidence:
            conn.execute("insert into evidence_revisions (revision_id,session_id,schema_version,payload_json,created_at) values (?,?,?,?,?)",
                         (f"revision_{n}", f"ses_{n}", "test", "{}", "now"))
        conn.execute("insert into session_evidence_heads (session_id,revision_id,updated_at) values (?,?,?)",
                     (f"ses_{n}", f"revision_{n}", "now"))
        spans.append({"session_id": f"ses_{n}", "segment_ids": ["other"] if partial and n == 1 else [f"seg_{n}"],
                      "source_revision_id": "old" if stale and n == 1 else f"revision_{n}"})
    conn.execute("insert into logical_meetings (meeting_id,title,state,current_revision_id,created_at,updated_at) values (?,?,?,?,?,?)",
                 ("meeting_1", "晚间会议", "accepted", "meeting_rev", "now", "now"))
    conn.execute("insert into logical_meeting_revisions (revision_id,meeting_id,payload_json,created_at) values (?,?,?,?)",
                 ("meeting_rev", "meeting_1", json.dumps({"spans": spans}), "now"))
    conn.commit()


def test_continuous_recordings_show_as_one_group_without_changing_sessions(tmp_path: Path) -> None:
    config, conn = _seed(tmp_path)
    before = [tuple(row) for row in conn.execute("select segment_id,session_id from transcript_segments order by segment_id")]
    groups = session_groups(conn, ["ses_0", "ses_1", "ses_2"])
    assert len(groups) == 1 and groups[0]["source"] == "continuous"
    assert groups[0]["session_ids"] == ["ses_0", "ses_1", "ses_2"]
    assert groups[0]["segment_count"] == 3
    assert [tuple(row) for row in conn.execute("select segment_id,session_id from transcript_segments order by segment_id")] == before
    conn.close()
    client = TestClient(create_app(config=config))
    day = client.get("/api/transcripts/days").json()["days"][0]
    assert day["session_count"] == 3 and day["meeting_count"] == 1
    assert client.get("/api/transcripts/days/2087-05-10/sessions").json()["meeting_groups"][0]["session_ids"] == ["ses_0", "ses_1", "ses_2"]
    assert client.get("/api/inbox").json()["meeting_groups"][0]["segment_count"] == 3


def test_confirmed_group_preserves_pinned_session_identity_and_boundaries(tmp_path: Path) -> None:
    _, conn = _seed(tmp_path)
    conn.execute("update sessions set name='原有名字' where session_id='ses_0'")
    _accepted(conn)
    group = session_groups(conn, ["ses_0", "ses_1", "ses_2"])[0]
    assert group["source"] == "confirmed" and group["title"] == "晚间会议"
    assert conn.execute("select name from sessions where session_id='ses_0'").fetchone()[0] == "原有名字"
    conn.close()


def test_partial_or_stale_reviewed_spans_are_never_silently_grouped(tmp_path: Path) -> None:
    for mode in ("partial", "stale"):
        _, conn = _seed(tmp_path / mode)
        _accepted(conn, partial=mode == "partial", stale=mode == "stale")
        assert session_groups(conn, ["ses_0", "ses_1", "ses_2"]) == []
        conn.close()


def test_different_device_or_named_boundaries_do_not_auto_join(tmp_path: Path) -> None:
    _, conn = _seed(tmp_path)
    conn.execute("update audio_files set source_device='another recorder' where audio_file_id='aud_1'")
    assert session_groups(conn, ["ses_0", "ses_1", "ses_2"]) == []
    conn.execute("update audio_files set source_device='recorder' where audio_file_id='aud_1'")
    conn.execute("update sessions set name='用户标记的独立会议' where session_id='ses_1'")
    assert session_groups(conn, ["ses_0", "ses_1", "ses_2"]) == []
    conn.close()
