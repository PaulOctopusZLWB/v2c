from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.conversation_dynamics import session_dynamics
from personal_context_node.storage.sqlite import connect, initialize


def _setup_session(database_path: Path) -> None:
    """Insert one session with a known speaker sequence A,A,B,A,C and deterministic durations.

    Absolute starts are spaced so the order is unambiguous; talk durations (end-start) are:
      A:1000, A:1000, B:2000, A:3000, C:4000  ->  A talk 5000, B 2000, C 4000, total 11000.
    Turns (maximal same-label runs in time order): [A,A], [B], [A], [C]  ->
      A has 2 turns, B 1, C 1. Transitions: A->B, B->A, A->C.
    """
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_d", "dev", "/s/d.wav", 1, 1, "/r/d.wav", "sha256:d", 20000, "2026-06-01T08:00:00+08:00", "2026-06-01T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_d", "2026-06-01", "2026-06-01T08:00:00+08:00", "2026-06-01T08:00:20+08:00", "derived", 5, 11000, "seg_a1", "2026-06-01T08:00:21+08:00", "2026-06-01T08:00:21+08:00"),
        )
        rows = [
            # segment_id, speaker, abs_start, start_ms, end_ms
            ("seg_a1", "A", "2026-06-01T08:00:00.000+08:00", 0, 1000),
            ("seg_a2", "A", "2026-06-01T08:00:02.000+08:00", 2000, 3000),
            ("seg_b1", "B", "2026-06-01T08:00:04.000+08:00", 4000, 6000),
            ("seg_a3", "A", "2026-06-01T08:00:07.000+08:00", 7000, 10000),
            ("seg_c1", "C", "2026-06-01T08:00:11.000+08:00", 11000, 15000),
        ]
        for seg_id, speaker, abs_start, start_ms, end_ms in rows:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seg_id, "aud_d", "chk", "ses_d", start_ms, end_ms, abs_start, "x", "zh", speaker, speaker, f"ev_{seg_id}", 1.0, "mock", "mock", "mock", 1, "2026-06-01T08:00:21+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def test_session_dynamics_talk_shares_turns_and_transitions(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _setup_session(config.database_path)

    result = session_dynamics(config=config, session_id="ses_d")

    assert result["session_id"] == "ses_d"
    assert result["total_ms"] == 11000

    speakers = {s["label"]: s for s in result["speakers"]}
    # speakers sorted by talk_ms desc: A(5000), C(4000), B(2000)
    assert [s["label"] for s in result["speakers"]] == ["A", "C", "B"]

    assert speakers["A"]["talk_ms"] == 5000
    assert speakers["A"]["segment_count"] == 3
    assert speakers["A"]["turns"] == 2  # [A,A] and [A]
    assert speakers["A"]["avg_segment_ms"] == round(5000 / 3, 3)
    assert speakers["A"]["talk_share"] == round(5000 / 11000, 3)

    assert speakers["B"]["talk_ms"] == 2000
    assert speakers["B"]["turns"] == 1
    assert speakers["B"]["talk_share"] == round(2000 / 11000, 3)

    assert speakers["C"]["talk_ms"] == 4000
    assert speakers["C"]["turns"] == 1

    # Transitions over consecutive turns: A->B, B->A, A->C
    transitions = [(t["from"], t["to"], t["count"]) for t in result["transitions"]]
    assert ("A", "B", 1) in transitions
    assert ("B", "A", 1) in transitions
    assert ("A", "C", 1) in transitions
    assert len(transitions) == 3


def test_session_dynamics_timeline_is_relative_and_merges_runs(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _setup_session(config.database_path)

    result = session_dynamics(config=config, session_id="ses_d")
    timeline = result["timeline"]

    # 4 turns: [A,A], [B], [A], [C]
    assert [t["label"] for t in timeline] == ["A", "B", "A", "C"]
    # First turn starts at 0 (relative to the session's first absolute start).
    assert timeline[0]["start_ms_rel"] == 0
    # First turn merges seg_a1+seg_a2: spans 08:00:00.000 -> 08:00:03.000 (start + last seg's
    # talk duration). The merged turn carries both segment ids.
    assert timeline[0]["segment_ids"] == ["seg_a1", "seg_a2"]
    assert timeline[0]["end_ms_rel"] == 3000  # 2000ms offset of seg_a2 start + its 1000ms talk
    # B turn starts 4000ms after the session start.
    assert timeline[1]["label"] == "B"
    assert timeline[1]["start_ms_rel"] == 4000
    # Timeline turns are in chronological order.
    starts = [t["start_ms_rel"] for t in timeline]
    assert starts == sorted(starts)


def test_session_dynamics_attribution_override_relabels(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _setup_session(config.database_path)

    # Override seg_b1 to person 张三: it should surface under label "张三", not "B".
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, 0, ?, ?)",
            ("per_z", "张三", "contact", "2026-06-01T08:00:00+08:00", "2026-06-01T08:00:00+08:00"),
        )
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, person_id, updated_at) values (?, ?, ?, ?)",
            ("seg_b1", "张三", "per_z", "2026-06-01T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()

    result = session_dynamics(config=config, session_id="ses_d")
    labels = {s["label"] for s in result["speakers"]}
    assert "张三" in labels
    assert "B" not in labels
    zhang = next(s for s in result["speakers"] if s["label"] == "张三")
    assert zhang["talk_ms"] == 2000


def test_session_dynamics_empty_session(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    initialize(connect(config.database_path))  # create schema, no rows

    result = session_dynamics(config=config, session_id="missing")
    assert result == {
        "session_id": "missing",
        "total_ms": 0,
        "speakers": [],
        "transitions": [],
        "timeline": [],
    }
