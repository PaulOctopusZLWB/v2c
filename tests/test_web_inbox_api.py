from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def _seed(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_a', 'Alice', 'contact', 0, 'now', 'now')")
        conn.execute("insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('aud_1', 'dev', '/tmp/a.wav', '/tmp/a.wav', 'sha', 1000, '2087-05-10T08:00:00+08:00', 'now', 'imported')")
        for n, (ses, started) in enumerate([("ses_old", "2087-05-10T08:00:00+08:00"), ("ses_new", "2087-05-10T14:00:00+08:00")]):
            first = f"seg_{n}0"
            conn.execute(
                "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, '2087-05-10', ?, ?, 'derived', 2, 1000, ?, 'now', 'now')",
                (ses, started, started, first),
            )
            for i in range(2):
                seg = f"seg_{n}{i}"
                conn.execute(
                    "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active) values (?, 'aud_1', ?, ?, ?, ?, 'text', 'zh', ?, ?, ?, 1)",
                    (seg, f"chk_{seg}", ses, i * 1000, (i + 1) * 1000, "self" if i == 0 else "spk_01", "self" if i == 0 else "spk_01", f"ev_{seg}"),
                )
        # ses_new: one voice attributed to Alice + marked present.
        conn.execute("insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values ('seg_11', 'Alice', 'now', 'per_a', 'manual')")
        conn.execute("insert into session_participants (session_id, person_id, status, source, updated_at) values ('ses_new', 'per_a', 'present', 'manual', 'now')")
        conn.commit()
    finally:
        conn.close()


def test_inbox_lists_recent_sessions_with_state_and_finalize_flow(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config.database_path)
    client = TestClient(create_app(config=config))

    inbox = client.get("/api/inbox").json()
    assert inbox["pending"] == 2
    assert [s["session_id"] for s in inbox["sessions"]] == ["ses_new", "ses_old"]  # newest first
    newest = inbox["sessions"][0]
    assert newest["present"] == ["Alice"]
    assert newest["attributed_count"] == 1
    assert newest["unidentified_count"] == 0  # 2 segments: 1 attributed + 1 self
    assert newest["finalized"] is None
    assert inbox["sessions"][1]["unidentified_count"] == 1  # ses_old's spk voice needs a verdict

    # Finalize the reviewed session; the inbox reflects it.
    assert client.post("/api/sessions/ses_new/finalize").status_code == 200
    after = client.get("/api/inbox").json()
    assert after["pending"] == 1
    finalized = next(s for s in after["sessions"] if s["session_id"] == "ses_new")
    assert finalized["finalized"] is not None
    assert finalized["finalized"]["export_md_path"].endswith("ses_new.md")
