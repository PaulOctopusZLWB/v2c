from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRResult, ASRSegment
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.transcription import transcribe_audio_file_diarized


class FakeDiarizedASRAdapter:
    """Whole-file diarized ASR stub: segments carry .speaker + ABSOLUTE-file ms."""

    model_name = "fake-diarized-asr"
    model_version = "test"

    def __init__(self, segments: list[ASRSegment]) -> None:
        self._segments = segments

    def transcribe(self, audio_path: Path) -> ASRResult:
        return ASRResult(
            segments=list(self._segments),
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
            language="zh",
        )


def _seed_audio_file(
    *,
    database_path: Path,
    audio_file_id: str = "aud_diar",
    local_raw_path: str = "/local/diar.wav",
    recorded_at: str = "2026-06-14T09:00:00+08:00",
) -> None:
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
                audio_file_id,
                "DJI Mic 3",
                "/source/diar.wav",
                local_raw_path,
                "sha256:diar",
                60_000,
                recorded_at,
                "2026-06-14T09:10:00+08:00",
                "imported",
            ),
        )
        conn.commit()
    finally:
        conn.close()


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


def test_diarized_multi_speaker_writes_clusters_and_equal_speaker_columns(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_audio_file(database_path=config.database_path)
    asr = FakeDiarizedASRAdapter(_segments(["spk_01", "spk_02", "spk_01"]))

    transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id="aud_diar")

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select speaker, speaker_cluster_id, text, start_ms, is_active
            from transcript_segments
            where audio_file_id = 'aud_diar' and is_active = 1
            order by start_ms
            """,
        )
        clusters = fetch_all(
            conn,
            "select speaker_cluster_id, label, source_type, source_ref from speaker_clusters order by speaker_cluster_id",
        )
    finally:
        conn.close()

    assert len(rows) == 3
    assert [row["speaker"] for row in rows] == ["spk_01", "spk_02", "spk_01"]
    # speaker and speaker_cluster_id MUST stay equal (review path joins on speaker,
    # attribution view on speaker_cluster_id).
    for row in rows:
        assert row["speaker"] == row["speaker_cluster_id"]

    cluster_ids = {row["speaker_cluster_id"] for row in clusters}
    assert cluster_ids == {"spk_01", "spk_02"}
    for row in clusters:
        assert row["label"] == row["speaker_cluster_id"]
        assert row["source_type"] == "diarization"
        assert row["source_ref"] == "aud_diar"


def test_diarized_single_speaker_self_creates_no_spk_clusters(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_audio_file(database_path=config.database_path)
    asr = FakeDiarizedASRAdapter(_segments(["self", "self", "self"]))

    transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id="aud_diar")

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select speaker, speaker_cluster_id
            from transcript_segments
            where audio_file_id = 'aud_diar' and is_active = 1
            order by start_ms
            """,
        )
        clusters = fetch_all(conn, "select speaker_cluster_id from speaker_clusters")
    finally:
        conn.close()

    assert len(rows) == 3
    for row in rows:
        assert row["speaker"] == "self"
        assert row["speaker_cluster_id"] == "self"
    # No cluster row for "self" — preserves the single-owner default-self prior.
    assert all(not str(row["speaker_cluster_id"]).startswith("spk_") for row in clusters)
    assert "self" not in {row["speaker_cluster_id"] for row in clusters}


def test_diarized_rerun_is_idempotent_no_duplicate_active_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_audio_file(database_path=config.database_path)
    asr = FakeDiarizedASRAdapter(_segments(["spk_01", "spk_02", "spk_01"]))

    transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id="aud_diar")
    transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id="aud_diar")

    conn = connect(config.database_path)
    try:
        active = fetch_all(
            conn,
            "select segment_id from transcript_segments where audio_file_id = 'aud_diar' and is_active = 1",
        )
        inactive = fetch_all(
            conn,
            "select segment_id from transcript_segments where audio_file_id = 'aud_diar' and is_active = 0",
        )
    finally:
        conn.close()

    # Re-run safe: only one active segment set (3 rows); the first run's rows were deactivated.
    assert len(active) == 3
    assert len(inactive) == 3


def test_diarized_multi_session_keeps_session_ids_stable_across_rerun(tmp_path: Path) -> None:
    # A single whole-file diarized recording with an internal silence gap >
    # session_gap_minutes splits into 2 sessions. Because each diarized session must own
    # a DISTINCT, DETERMINISTIC first-chunk_id, an ASR re-run (which replaces every
    # segment id) must keep BOTH session ids stable — anchored on the per-segment
    # synthetic chunk_id. A single file-wide chunk_id collapses the reuse anchor and
    # mints fresh ids on every re-run (orphaning ses_*.md notes / [[ses_*]] backlinks).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_audio_file(database_path=config.database_path, recorded_at="2026-06-14T09:00:00+08:00")
    day = "2026-06-14"
    gap_minutes = 20
    gap_ms = (gap_minutes + 5) * 60 * 1000  # comfortably above the gap threshold

    # Two blocks separated by a > gap silence: forces _group_segments into 2 sessions.
    segments = [
        ASRSegment(text="a0", start_ms=0, end_ms=500, language="zh", speaker="self"),
        ASRSegment(text="a1", start_ms=1_000, end_ms=1_500, language="zh", speaker="self"),
        ASRSegment(text="b0", start_ms=gap_ms, end_ms=gap_ms + 500, language="zh", speaker="self"),
        ASRSegment(text="b1", start_ms=gap_ms + 1_000, end_ms=gap_ms + 1_500, language="zh", speaker="self"),
    ]
    asr = FakeDiarizedASRAdapter(segments)

    transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id="aud_diar")
    derive_sessions_for_day(config=config, day=day, session_gap_minutes=gap_minutes)
    before = _session_ids_by_start(config.database_path)
    assert len(before) == 2  # the internal gap split the file into 2 sessions

    # Re-run BOTH stages (ASR re-run replaces every segment id, then re-derive).
    transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id="aud_diar")
    derive_sessions_for_day(config=config, day=day, session_gap_minutes=gap_minutes)
    after = _session_ids_by_start(config.database_path)

    assert after == before  # both session ids stable across the ASR re-run


def _session_ids_by_start(database_path: Path) -> list[str]:
    conn = connect(database_path)
    try:
        rows = fetch_all(conn, "select session_id from sessions order by started_at")
    finally:
        conn.close()
    return [str(row["session_id"]) for row in rows]
