from __future__ import annotations

from pathlib import Path

from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRResult, ASRSegment
from personal_context_node.process_runner import drain_process_queue
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task_in_conn, process_status_rows


class PathDispatchingDiarizedASR:
    """Fake diarized ASR: returns per-file segments keyed on the audio path it is given.

    transcribe_audio_file_diarized calls `asr.transcribe(local_raw_path)`, so dispatching on
    that path lets ONE adapter serve a multi-file day with different speaker mixes per file —
    the realistic diarize case (some recordings multi-speaker, some single-speaker/self).
    """

    model_name = "fake-diarized-asr"
    model_version = "test"

    def __init__(self, segments_by_path: dict[str, list[ASRSegment]]) -> None:
        self._by_path = segments_by_path

    def transcribe(self, audio_path: Path) -> ASRResult:
        segments = self._by_path[str(audio_path)]
        return ASRResult(
            segments=list(segments),
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
            language="zh",
        )


def _segments(speakers: list[str]) -> list[ASRSegment]:
    return [
        ASRSegment(
            text=f"line {index}",
            start_ms=index * 1000,
            end_ms=index * 1000 + 500,
            language="zh",
            speaker=speaker,
        )
        for index, speaker in enumerate(speakers)
    ]


def _seed_audio_file(*, conn, audio_file_id: str, recorded_at: str, local_raw_path: Path) -> None:
    local_raw_path.parent.mkdir(parents=True, exist_ok=True)
    local_raw_path.write_bytes(b"raw")
    conn.execute(
        """
        insert into audio_files (
          audio_file_id, source_device, source_path, local_raw_path, sha256,
          duration_ms, recorded_at, imported_at, status
        ) values (?, 'DJI Mic 3', ?, ?, ?, 60000, ?, ?, 'imported')
        """,
        (
            audio_file_id,
            f"/source/{audio_file_id}.wav",
            str(local_raw_path),
            f"sha256:{audio_file_id}",
            recorded_at,
            recorded_at,
        ),
    )


def test_diarize_pipeline_drains_end_to_end_with_real_speaker_clusters(tmp_path: Path) -> None:
    # End-to-end: a multi-file, multi-speaker DAY flows import -> transcribe_diarize ->
    # session_derive -> summarize_session -> daily_generate -> obsidian_publish exactly once,
    # under MOCK models (no funasr, no network). File A has two real diarization clusters
    # (spk_01/spk_02), file B is single-speaker (self). The whole-day round-7 invariant must
    # hold: ONE derive, ONE published daily note — no premature/partial then redundant re-run.
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "PersonalContext",
        asr_mode="diarize",
        # end-to-end drain: keep the old auto-chain so the day reaches published notes.
        pipeline_auto_viewpoints=True,
    )

    raw_a = config.data_dir / "raw" / "2026-06-14" / "fileA.wav"
    raw_b = config.data_dir / "raw" / "2026-06-14" / "fileB.wav"

    conn = connect(config.database_path)
    try:
        initialize(conn)
        # Two recordings on the SAME day, different HHMMSS — the normal multi-recording day.
        _seed_audio_file(conn=conn, audio_file_id="aud_A", recorded_at="2026-06-14T09:00:00+08:00", local_raw_path=raw_a)
        _seed_audio_file(conn=conn, audio_file_id="aud_B", recorded_at="2026-06-14T15:00:00+08:00", local_raw_path=raw_b)
        # Mirror diarize ingest: one transcribe_diarize task per audio_file (date-major priority).
        enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_A", priority=1)
        enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_B", priority=1)
        conn.commit()
    finally:
        conn.close()

    # File A: two distinct speakers across three segments. File B: all self (single speaker).
    asr = PathDispatchingDiarizedASR(
        {
            str(raw_a): _segments(["spk_01", "spk_02", "spk_01"]),
            str(raw_b): _segments(["self", "self"]),
        }
    )

    result = drain_process_queue(
        config=config,
        vad=MockVADAdapter(),  # unused in diarize mode
        asr=asr,
        llm=MockLLMAdapter(),
    )
    assert result.status == "complete"
    assert result.tasks_failed == 0

    conn = connect(config.database_path)
    try:
        seg_a = fetch_all(
            conn,
            "select speaker, speaker_cluster_id, text from transcript_segments"
            " where audio_file_id = 'aud_A' and is_active = 1 order by start_ms",
        )
        seg_b = fetch_all(
            conn,
            "select speaker, speaker_cluster_id from transcript_segments"
            " where audio_file_id = 'aud_B' and is_active = 1 order by start_ms",
        )
        clusters = fetch_all(
            conn,
            "select speaker_cluster_id, source_type from speaker_clusters order by speaker_cluster_id",
        )
        sessions = fetch_all(conn, "select date_key, segment_count from sessions")
        daily_summaries = fetch_all(
            conn,
            "select target_id from summaries where summary_type = 'daily' and target_type = 'date_key'"
            " and prompt_version = 'llm_port.daily_summary.v1'",
        )
        report = fetch_all(conn, "select note_path from daily_reports where date_key = '2026-06-14'")
    finally:
        conn.close()

    # (1) File A -> 3 active segments with speaker == speaker_cluster_id ∈ {spk_01, spk_02};
    #     file B -> all "self".
    assert [s["speaker"] for s in seg_a] == ["spk_01", "spk_02", "spk_01"]
    for s in seg_a:
        assert s["speaker"] == s["speaker_cluster_id"]
        assert s["speaker"] in {"spk_01", "spk_02"}
    assert [s["speaker"] for s in seg_b] == ["self", "self"]
    for s in seg_b:
        assert s["speaker"] == s["speaker_cluster_id"] == "self"

    # (2) speaker_clusters has rows for spk_01 and spk_02 (source_type='diarization'); none for self.
    assert clusters == [
        {"speaker_cluster_id": "spk_01", "source_type": "diarization"},
        {"speaker_cluster_id": "spk_02", "source_type": "diarization"},
    ]

    # (3) The day's session_derive ran ONCE over the WHOLE day (all 5 segments across both files)
    #     and reached daily_generate; EXACTLY ONE daily note published (round-7: no redundant
    #     re-derive/re-publish). The two recordings are 6h apart (> 20min gap) so the single derive
    #     correctly yields two same-day sessions — both keyed to 2026-06-14.
    assert {r["date_key"] for r in sessions} == {"2026-06-14"}
    assert sum(int(r["segment_count"]) for r in sessions) == 5
    assert [r["target_id"] for r in daily_summaries] == ["2026-06-14"]

    note_path = config.obsidian_vault / "10_Daily" / "2026-06-14.md"
    assert note_path.exists()
    assert report and report[0]["note_path"] == str(note_path)
    daily_notes = list((config.obsidian_vault / "10_Daily").glob("*.md"))
    assert daily_notes == [note_path]  # exactly one daily note on disk

    # (4) Every task for the day settled succeeded; none stuck pending/running.
    statuses = process_status_rows(config=config)
    assert statuses, "expected the day's pipeline to have produced tasks"
    pipeline_types = {
        "transcribe_diarize",
        "session_derive",
        "summarize_session",
        "daily_generate",
        "obsidian_publish",
    }
    pipeline_tasks = [r for r in statuses if r["task_type"] in pipeline_types]
    assert pipeline_tasks
    assert all(r["status"] == "succeeded" for r in pipeline_tasks), [
        (r["task_type"], r["status"], r["last_error"]) for r in pipeline_tasks if r["status"] != "succeeded"
    ]
    # Each fan-in stage produced EXACTLY ONE whole-day task (no duplicate/again).
    for task_type, target_id in [
        ("session_derive", "2026-06-14"),
        ("daily_generate", "2026-06-14"),
        ("obsidian_publish", "2026-06-14"),
    ]:
        matching = [r for r in statuses if r["task_type"] == task_type and r["target_id"] == target_id]
        assert len(matching) == 1, (task_type, target_id, matching)
