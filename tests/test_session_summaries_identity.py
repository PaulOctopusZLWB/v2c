from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import SessionSummary
from personal_context_node.identity_review import set_session_participant
from personal_context_node.session_summaries import summarize_session
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


class RecordingSafeLLM:
    def __init__(self) -> None:
        self.segments: list[dict[str, object]] = []
        self.prompt: str | None = None

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None) -> SessionSummary:
        self.segments = transcript_segments
        self.prompt = prompt
        return SessionSummary(
            session_id=session_id,
            headline="safe",
            summary="safe",
            topics=[],
            decisions=[],
            todos=[],
            open_questions=[],
        )


def test_summarize_session_uses_confirmed_participants_and_v2_without_overwriting_v1(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", send_speaker_labels=True)
    _seed_summary_identity_session(config.database_path)
    set_session_participant(config=config, session_id="ses_1", person_id="per_a", status="present")
    set_session_participant(config=config, session_id="ses_1", person_id="per_b", status="present")
    set_session_participant(config=config, session_id="ses_1", person_id="per_c", status="absent")
    llm = RecordingSafeLLM()

    result = summarize_session(config=config, session_id="ses_1", llm=llm)

    assert result.summaries_created == 1
    assert [segment["speaker"] for segment in llm.segments] == ["Alice", "未确认说话人_1", "Bob"]
    assert "本场确认出现的人物: Alice, Bob" in (llm.prompt or "")
    assert "不得输出未出现在确认名单中的人物姓名" in (llm.prompt or "")
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select prompt_version, content_json from summaries order by prompt_version")
    finally:
        conn.close()
    assert [row["prompt_version"] for row in rows] == ["llm_port.session_summary.v1", "llm_port.session_summary.v2"]
    assert json.loads(str(rows[1]["content_json"]))["schema_version"] == "session_summary.v2"


def _seed_summary_identity_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        for person_id, label in [("per_a", "Alice"), ("per_b", "Bob"), ("per_c", "Carol")]:
            conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, 'contact', 0, 'now', 'now')", (person_id, label))
        conn.execute("insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('aud_1', 'dev', '/tmp/a.wav', '/tmp/a.wav', 'sha', 3000, '2087-05-10T08:00:00+08:00', 'now', 'imported')")
        conn.execute("insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values ('ses_1', '2087-05-10', '2087-05-10T08:00:00+08:00', '2087-05-10T08:01:00+08:00', 'derived', 3, 3000, 'seg_1', 'now', 'now')")
        for idx, (segment_id, speaker, text, person_id, person_label) in enumerate([
            ('seg_1', 'spk_01', 'a', 'per_a', 'Alice'),
            ('seg_2', 'spk_02', 'c', 'per_c', 'Carol'),
            ('seg_3', 'spk_03', 'b', 'per_b', 'Bob'),
        ]):
            conn.execute("insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active) values (?, 'aud_1', ?, 'ses_1', ?, ?, ?, 'zh', ?, ?, ?, 1)", (segment_id, f'chk_{segment_id}', idx * 1000, (idx + 1) * 1000, text, speaker, speaker, f'ev_{segment_id}'))
            conn.execute("insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, ?, 'now', ?, 'manual')", (segment_id, person_label, person_id))
        conn.execute("insert into summaries (summary_id, summary_type, target_type, target_id, prompt_version, model_name, content_json, created_at, updated_at) values ('sum_old', 'session', 'session', 'ses_1', 'llm_port.session_summary.v1', 'mock', '{""schema_version"":""session_summary.v1"",""session_id"":""ses_1"",""headline"":""old"",""summary"":""old"",""topics"":[],""decisions"":[],""todos"":[],""open_questions"":[],""core_conclusions"":[],""per_speaker"":[]}', 'old', 'old')")
        conn.commit()
    finally:
        conn.close()
