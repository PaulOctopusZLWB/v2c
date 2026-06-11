from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_sessions import publish_session_notes
from personal_context_node.session_summaries import summarize_session
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


class RecordingSessionLLM:
    def __init__(self) -> None:
        self.received_segments: list[dict[str, object]] = []

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]):
        self.received_segments = transcript_segments
        return RuleBasedLLMAdapter().generate_session_summary(
            session_id=session_id,
            transcript_segments=transcript_segments,
        )


def test_summarize_session_persists_schema_and_renders_note(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)

    result = summarize_session(config=config, session_id="ses_test", llm=RuleBasedLLMAdapter())

    assert result.summaries_created == 1
    conn = connect(config.database_path)
    try:
        summaries = fetch_all(
            conn,
            """
            select summary_type, target_type, target_id, prompt_version, content_json
            from summaries
            """,
        )
    finally:
        conn.close()

    assert summaries[0]["summary_type"] == "session"
    assert summaries[0]["target_type"] == "session"
    assert summaries[0]["target_id"] == "ses_test"
    assert summaries[0]["prompt_version"] == "llm_port.session_summary.v1"
    content = json.loads(str(summaries[0]["content_json"]))
    assert content["schema_version"] == "session_summary.v1"
    assert content["session_id"] == "ses_test"
    assert "决定继续接入真实 ASR" in content["summary"]
    assert content["decisions"] == [
        {"text": "我决定继续接入真实 ASR，需要保持音频本地处理。", "evidence_refs": ["ev_1"]}
    ]
    assert content["todos"] == [
        {"text": "保持音频本地处理", "owner": "self", "evidence_refs": ["ev_1"]}
    ]

    publish_session_notes(config=config, day="2087-05-10")
    note = (config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md").read_text(encoding="utf-8")
    assert "## 我决定继续接入真实 ASR，需要保持音频本地处理。" in note
    assert "三段以内摘要" not in note
    assert "- Decision: 我决定继续接入真实 ASR，需要保持音频本地处理。" in note
    assert "- Todo: 保持音频本地处理 (owner: self)" in note
    assert "完整转写不进入 session note" in note


def test_summarize_session_omits_speaker_labels_when_disabled(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        send_speaker_labels=False,
    )
    _insert_session_and_segments(config.database_path)

    llm = RecordingSessionLLM()
    summarize_session(config=config, session_id="ses_test", llm=llm)

    assert llm.received_segments == [
        {
            "segment_id": "seg_1",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "我决定继续接入真实 ASR，需要保持音频本地处理。",
            "evidence_id": "ev_1",
        },
        {
            "segment_id": "seg_2",
            "start_ms": 1000,
            "end_ms": 2000,
            "text": "faster-whisper 备选是否需要提前装好",
            "evidence_id": "ev_2",
        },
    ]


def _insert_session_and_segments(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
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
        for segment_id, text, evidence_id in [
            ("seg_1", "我决定继续接入真实 ASR，需要保持音频本地处理。", "ev_1"),
            ("seg_2", "faster-whisper 备选是否需要提前装好", "ev_2"),
        ]:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_test",
                    f"chk_{segment_id}",
                    "ses_test",
                    0 if segment_id == "seg_1" else 1000,
                    1000 if segment_id == "seg_1" else 2000,
                    text,
                    "zh",
                    "self",
                    evidence_id,
                    0.99,
                    "MockASRAdapter",
                    "mock-asr",
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()
