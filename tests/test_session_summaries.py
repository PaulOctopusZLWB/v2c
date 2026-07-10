from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import (
    SessionDecision,
    SessionSummary,
    SpeakerAnalysis,
    SpeakerViewpoint,
)
from personal_context_node.obsidian_sessions import publish_session_notes
from personal_context_node.session_summaries import summarize_session
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


class RecordingSessionLLM:
    def __init__(self) -> None:
        self.received_segments: list[dict[str, object]] = []

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None):
        self.received_segments = transcript_segments
        return RuleBasedLLMAdapter().generate_session_summary(
            session_id=session_id,
            transcript_segments=transcript_segments,
        )


class CountingSessionLLM:
    """Delegates to RuleBasedLLMAdapter but counts how many times it was actually invoked."""

    def __init__(self) -> None:
        self.call_count = 0

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None):
        self.call_count += 1
        return RuleBasedLLMAdapter().generate_session_summary(
            session_id=session_id,
            transcript_segments=transcript_segments,
        )


class ChunkRecordingSessionLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None) -> SessionSummary:
        self.calls.append((session_id, transcript_segments))
        return SessionSummary(
            session_id=session_id,
            headline=f"headline {session_id}",
            summary=" / ".join(str(segment["text"]) for segment in transcript_segments),
            topics=[],
            decisions=[],
            todos=[],
            open_questions=[],
        )


class UnknownEvidenceSessionLLM:
    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None) -> SessionSummary:
        return SessionSummary(
            session_id=session_id,
            headline="未知证据引用",
            summary="未知证据引用。",
            topics=[],
            decisions=[SessionDecision(text="不应保存未知证据决策", evidence_refs=["ev_missing"])],
            todos=[],
            open_questions=[],
        )


class MissingEvidenceSessionLLM:
    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None) -> SessionSummary:
        return SessionSummary(
            session_id=session_id,
            headline="缺少证据引用",
            summary="缺少证据引用。",
            topics=[],
            decisions=[SessionDecision(text="不应保存无证据决策", evidence_refs=[])],
            todos=[],
            open_questions=[],
        )


class PerSpeakerSessionLLM:
    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]], prompt: str | None = None) -> SessionSummary:
        return SessionSummary(
            session_id=session_id,
            headline="带说话人分析",
            summary="带说话人分析的摘要。",
            topics=[],
            decisions=[],
            todos=[],
            open_questions=[],
            core_conclusions=["核心结论"],
            per_speaker=[
                SpeakerAnalysis(
                    speaker_cluster_id="spk_01",
                    viewpoints=[SpeakerViewpoint(text="观点", evidence_refs=["ev_1"])],
                    sentiment="积极",
                    stance="支持",
                    latent_needs=["更快"],
                )
            ],
        )


def test_summarize_session_persists_per_speaker_and_core_conclusions(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)

    result = summarize_session(config=config, session_id="ses_test", llm=PerSpeakerSessionLLM())

    assert result.summaries_created == 1
    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select content_json from summaries")
    finally:
        conn.close()
    content = json.loads(str(summaries[0]["content_json"]))
    assert content["core_conclusions"] == ["核心结论"]
    assert content["per_speaker"] == [
        {
            "speaker_cluster_id": "spk_01",
            "viewpoints": [{"text": "观点", "evidence_refs": ["ev_1"]}],
            "sentiment": "积极",
            "stance": "支持",
            "latent_needs": ["更快"],
        }
    ]


def test_summarize_session_does_not_hold_write_lock_during_llm_call(tmp_path: Path) -> None:
    """Regression: the WAL write lock must be released BEFORE the (slow) LLM call, so concurrent
    writers — manual viewpoint edit/publish, the segment-text PATCH — don't 500 with
    'database is locked'. We prove it by writing from a second connection (short busy_timeout)
    while the LLM is being called; if the lock were held across the call, this would fail."""
    import sqlite3

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)

    probe: dict[str, object] = {}

    class ConcurrentWriterLLM:
        def generate_session_summary(self, *, session_id: str, transcript_segments, prompt=None) -> SessionSummary:
            other = sqlite3.connect(config.database_path)
            other.execute("pragma busy_timeout = 500")
            try:
                other.execute(
                    "insert into session_viewpoint_state (session_id, status, updated_at) "
                    "values ('probe', 'draft', '2026-06-18T00:00:00+00:00')"
                )
                other.commit()
                probe["ok"] = True
            except sqlite3.OperationalError as exc:  # lock held across the LLM call → bug
                probe["ok"] = False
                probe["err"] = str(exc)
            finally:
                other.close()
            return SessionSummary(
                session_id=session_id,
                headline="h",
                summary="s",
                topics=[],
                decisions=[],
                todos=[],
                open_questions=[],
                core_conclusions=[],
                per_speaker=[],
            )

    result = summarize_session(config=config, session_id="ses_test", llm=ConcurrentWriterLLM())

    assert result.summaries_created == 1
    assert probe.get("ok") is True, probe.get("err")


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
    assert summaries[0]["prompt_version"] == "llm_port.session_summary.v2"
    content = json.loads(str(summaries[0]["content_json"]))
    assert content["schema_version"] == "session_summary.v2"
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
    # Per §29.7 the full transcript is not embedded in the session note.
    assert "## Transcript" not in note
    assert "- `00:00.000-00:01.000` **self**: 我决定继续接入真实 ASR，需要保持音频本地处理。" not in note


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


def test_summarize_session_mints_evidence_refs_before_prompting(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)

    result = summarize_session(config=config, session_id="ses_test", llm=RuleBasedLLMAdapter())

    assert result.summaries_created == 1
    conn = connect(config.database_path)
    try:
        evidence_refs = fetch_all(
            conn,
            """
            select evidence_id, source_type, source_id, source_ref, owner_id, quote
            from evidence_refs
            order by evidence_id
            """,
        )
    finally:
        conn.close()
    assert evidence_refs == [
        {
            "evidence_id": "ev_1",
            "source_type": "transcript_segment",
            "source_id": "seg_1",
            "source_ref": "seg_1",
            "owner_id": "did:key:local-owner",
            "quote": "我决定继续接入真实 ASR，需要保持音频本地处理。",
        },
        {
            "evidence_id": "ev_2",
            "source_type": "transcript_segment",
            "source_id": "seg_2",
            "source_ref": "seg_2",
            "owner_id": "did:key:local-owner",
            "quote": "faster-whisper 备选是否需要提前装好",
        },
    ]


def test_summarize_session_uses_chunk_summaries_when_text_exceeds_budget(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        max_chunk_tokens=5,
        send_speaker_labels=False,
    )
    _insert_session_and_segments(
        config.database_path,
        segments=[
            ("seg_1", "alpha beta gamma", "ev_1"),
            ("seg_2", "delta epsilon zeta", "ev_2"),
            ("seg_3", "eta theta", "ev_3"),
        ],
    )

    llm = ChunkRecordingSessionLLM()
    result = summarize_session(config=config, session_id="ses_test", llm=llm)

    assert result.summaries_created == 1
    assert [call[0] for call in llm.calls] == [
        "ses_test:chunk:1",
        "ses_test:chunk:2",
        "ses_test",
    ]
    assert [segment["segment_id"] for segment in llm.calls[0][1]] == ["seg_1"]
    assert [segment["segment_id"] for segment in llm.calls[1][1]] == ["seg_2", "seg_3"]
    assert llm.calls[2][1] == [
        {
            "segment_id": "ses_test_chunk_1",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "alpha beta gamma",
            "evidence_id": "ev_1",
        },
        {
            "segment_id": "ses_test_chunk_2",
            "start_ms": 1000,
            "end_ms": 3000,
            "text": "delta epsilon zeta / eta theta",
            "evidence_id": "ev_2",
        },
    ]

    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select summary_type, target_id, content_json from summaries")
    finally:
        conn.close()
    assert len(summaries) == 1
    assert summaries[0]["summary_type"] == "session"
    assert summaries[0]["target_id"] == "ses_test"


def test_summarize_session_rejects_unknown_evidence_refs_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)

    try:
        summarize_session(config=config, session_id="ses_test", llm=UnknownEvidenceSessionLLM())
    except ValueError as exc:
        assert "unknown evidence_id: ev_missing" in str(exc)
    else:
        raise AssertionError("summarize_session accepted an unknown evidence ref")

    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select summary_id from summaries")
    finally:
        conn.close()
    assert summaries == []


def test_summarize_session_rejects_empty_decision_evidence_refs_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)

    try:
        summarize_session(config=config, session_id="ses_test", llm=MissingEvidenceSessionLLM())
    except ValueError as exc:
        assert "missing evidence_refs" in str(exc)
    else:
        raise AssertionError("summarize_session accepted a decision without evidence refs")

    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select summary_id from summaries")
        evidence_refs = fetch_all(conn, "select evidence_id from evidence_refs")
    finally:
        conn.close()
    assert summaries == []
    assert evidence_refs == []


# --- fingerprint incremental skip -----------------------------------------


def test_summarize_session_skips_llm_when_fingerprint_unchanged(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)
    llm = CountingSessionLLM()

    first = summarize_session(config=config, session_id="ses_test", llm=llm)
    second = summarize_session(config=config, session_id="ses_test", llm=llm)

    assert first.summaries_created == 1
    assert second.summaries_created == 0
    assert llm.call_count == 1

    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select summary_id from summaries")
    finally:
        conn.close()
    # still exactly one summary row (no duplicate / no unnecessary rewrite).
    assert len(summaries) == 1


def test_summarize_session_recalls_llm_when_fingerprint_changes(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)
    llm = CountingSessionLLM()

    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert llm.call_count == 1

    # Edit a segment's text -> the fingerprint changes -> the next call must re-invoke the LLM.
    conn = connect(config.database_path)
    try:
        conn.execute("update transcript_segments set text = ? where segment_id = ?", ("修改后的文字", "seg_1"))
        conn.commit()
    finally:
        conn.close()

    result = summarize_session(config=config, session_id="ses_test", llm=llm)

    assert result.summaries_created == 1
    assert llm.call_count == 2


def test_summarize_session_force_bypasses_skip(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)
    llm = CountingSessionLLM()

    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert llm.call_count == 1

    # Transcript unchanged, but force=True must re-invoke the LLM anyway.
    result = summarize_session(config=config, session_id="ses_test", llm=llm, force=True)

    assert result.summaries_created == 1
    assert llm.call_count == 2


def test_summarize_session_recalls_llm_when_prompt_changes(tmp_path: Path) -> None:
    # Editing the prompt (session override or global template) must invalidate the
    # fingerprint skip even though the transcript segments are unchanged.
    from personal_context_node.session_viewpoint import (
        set_session_prompt_override,
        set_session_prompt_template,
    )

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_segments(config.database_path)
    llm = CountingSessionLLM()

    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert llm.call_count == 1

    set_session_prompt_override(config=config, session_id="ses_test", template="请用要点列表总结这次对话。")
    result = summarize_session(config=config, session_id="ses_test", llm=llm)
    assert result.summaries_created == 1
    assert llm.call_count == 2

    # Unchanged again -> skip.
    assert summarize_session(config=config, session_id="ses_test", llm=llm).summaries_created == 0
    assert llm.call_count == 2

    # A GLOBAL template edit must also invalidate (once the override is cleared it applies).
    set_session_prompt_override(config=config, session_id="ses_test", template=None)
    set_session_prompt_template(config=config, template="全局新模板:输出更精炼。")
    result = summarize_session(config=config, session_id="ses_test", llm=llm)
    assert result.summaries_created == 1
    assert llm.call_count == 3


def _insert_session_and_segments(
    database_path: Path,
    *,
    segments: list[tuple[str, str, str]] | None = None,
) -> None:
    segments = segments or [
        ("seg_1", "我决定继续接入真实 ASR，需要保持音频本地处理。", "ev_1"),
        ("seg_2", "faster-whisper 备选是否需要提前装好", "ev_2"),
    ]
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
        for index, (segment_id, text, evidence_id) in enumerate(segments):
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
                    index * 1000,
                    (index + 1) * 1000,
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
