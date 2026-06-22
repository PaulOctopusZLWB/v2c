from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRResult, ASRSegment
from personal_context_node.core.ports.file_import import (
    ImportedRawAudio,
    MountedDevice,
    SourceAudioFile,
    StableSourceAudioFile,
)
from personal_context_node.process_runner import (
    _ready_session_derive_dates_for_file,
    process_once,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task_in_conn, process_status_rows


class RecordingFileImporter:
    """Minimal FileImportPort stub (mirrors tests/test_ingest_file_import_port.py)."""

    def __init__(self, *, device: MountedDevice, source: SourceAudioFile) -> None:
        self.device = device
        self.source = source
        self.calls: list[str] = []

    def discover_devices(self) -> list[MountedDevice]:
        self.calls.append("discover_devices")
        return [self.device]

    def discover_audio_files(self, device: MountedDevice) -> list[SourceAudioFile]:
        self.calls.append(f"discover_audio_files:{device.device_id}")
        return [self.source]

    def wait_until_stable(self, source: SourceAudioFile, *, stable_seconds: int) -> StableSourceAudioFile:
        self.calls.append(f"wait_until_stable:{stable_seconds}")
        return StableSourceAudioFile(source=source, stable_checked_at=datetime.now(timezone.utc).isoformat())

    def copy_to_raw_store(self, source: StableSourceAudioFile, destination_dir: Path) -> ImportedRawAudio:
        self.calls.append("copy_to_raw_store")
        local_raw_path = destination_dir / "2025-06-10" / source.source.source_path.name
        local_raw_path.parent.mkdir(parents=True, exist_ok=True)
        local_raw_path.write_bytes(b"raw")
        return ImportedRawAudio(
            source=source,
            local_raw_path=local_raw_path,
            sha256="sha256:ported",
            duration_ms=1000,
            recorded_at="2025-06-10T17:35:50+08:00",
        )


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
    audio_file_id: str,
    recorded_at: str,
    local_raw_path: str | None = None,
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
                f"/source/{audio_file_id}.wav",
                local_raw_path or f"/local/{audio_file_id}.wav",
                f"sha256:{audio_file_id}",
                60_000,
                recorded_at,
                recorded_at,
                "imported",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _set_task_status(database_path: Path, *, task_id: str, status: str, retry_count: int = 0, max_retries: int = 3) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "update tasks set status = ?, retry_count = ?, max_retries = ? where task_id = ?",
            (status, retry_count, max_retries, task_id),
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


def _vad() -> MockVADAdapter:
    return MockVADAdapter()


def test_session_derive_fan_in_waits_for_all_same_day_files_diarize(tmp_path: Path) -> None:
    # Round-7 whole-day invariant, re-expressed per audio_file: session_derive (and everything
    # downstream) rebuilds the WHOLE day, so its transcribe_diarize fan-in must wait until EVERY
    # recording on that day has settled — not just the triggering file. Mirror of
    # test_process_runner_sessions::test_session_derive_fan_in_waits_for_all_same_day_files.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_1", recorded_at="2026-06-14T09:00:00+08:00")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_2", recorded_at="2026-06-14T15:00:00+08:00")

    conn = connect(config.database_path)
    try:
        t1 = enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1")
        t2 = enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_2")
        conn.commit()
    finally:
        conn.close()

    # file-1 succeeded but file-2 still pending -> the day is NOT ready for session_derive.
    _set_task_status(config.database_path, task_id=t1.task_id, status="succeeded")
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_1") == []

    # Finish file-2 -> now the whole day is ready.
    _set_task_status(config.database_path, task_id=t2.task_id, status="succeeded")
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_1") == ["2026-06-14"]


def test_terminally_failed_same_day_file_does_not_block_day(tmp_path: Path) -> None:
    # Liveness: a transcribe_diarize that has reached a terminal state (failed_terminal, or
    # failed_retryable with retries exhausted) is "done (failed)" and must not block the day forever.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_ok", recorded_at="2026-06-14T09:00:00+08:00")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_dead", recorded_at="2026-06-14T15:00:00+08:00")

    conn = connect(config.database_path)
    try:
        t_ok = enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_ok")
        t_dead = enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_dead")
        conn.commit()
    finally:
        conn.close()

    _set_task_status(config.database_path, task_id=t_ok.task_id, status="succeeded")
    # A terminally-failed sibling is settled.
    _set_task_status(config.database_path, task_id=t_dead.task_id, status="failed_terminal")
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_ok") == ["2026-06-14"]

    # Retry-exhausted (failed_retryable at max_retries) is likewise settled.
    _set_task_status(config.database_path, task_id=t_dead.task_id, status="failed_retryable", retry_count=3, max_retries=3)
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_ok") == ["2026-06-14"]


def _seed_segment(database_path: Path, *, segment_id: str, audio_file_id: str, speaker: str) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text, language,
              speaker, speaker_cluster_id, evidence_id, is_active
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (segment_id, audio_file_id, f"chk_{segment_id}", 0, 500, "hi", "zh", speaker, speaker, f"ev_{segment_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def _attribute_segment(database_path: Path, *, segment_id: str, person_id: str) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) "
            "values (?, ?, ?, ?, 'manual')",
            (segment_id, person_id, "2026-06-14T00:00:00+08:00", person_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_require_identified_speakers_gates_session_derive(tmp_path: Path) -> None:
    # Speaker-first: with the gate on, a day with any unattributed active segment is held at the
    # transcribe_diarize -> session_derive edge until every voice is identified. Diarize labels
    # (spk_NN) collide across files, so identity is per-segment attribution, not the raw label.
    config = AppConfig(
        data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault",
        asr_mode="diarize", require_identified_speakers=True,
    )
    config_off = AppConfig(
        data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize",
    )
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_1", recorded_at="2026-06-14T09:00:00+08:00")
    conn = connect(config.database_path)
    try:
        t1 = enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1")
        conn.commit()
    finally:
        conn.close()
    _set_task_status(config.database_path, task_id=t1.task_id, status="succeeded")
    _seed_segment(config.database_path, segment_id="seg_self", audio_file_id="aud_1", speaker="self")
    _seed_segment(config.database_path, segment_id="seg_spk1", audio_file_id="aud_1", speaker="spk_01")

    # Diarize settled, but speakers unidentified -> gate holds the day.
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_1") == []
    # With the gate OFF the same state is ready (default behavior unchanged).
    assert _ready_session_derive_dates_for_file(config=config_off, audio_file_id="aud_1") == ["2026-06-14"]

    # Identify one of two voices -> still gated (the other is unattributed).
    _attribute_segment(config.database_path, segment_id="seg_self", person_id="per_owner")
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_1") == []

    # Identify the last voice -> the day is released.
    _attribute_segment(config.database_path, segment_id="seg_spk1", person_id="per_alice")
    assert _ready_session_derive_dates_for_file(config=config, audio_file_id="aud_1") == ["2026-06-14"]


def test_ingest_in_diarize_mode_enqueues_transcribe_diarize_not_vad(tmp_path: Path) -> None:
    from personal_context_node.ingest import import_audio_files_from_port
    from personal_context_node.config import DeviceDiscoveryConfig

    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "mounted_dji")
    source = SourceAudioFile(
        device=device,
        source_path=device.root_path / "TX02_MIC001_20250610_173550_orig.wav",
        size_bytes=1024,
        mtime_ns=123456789,
    )

    # diarize mode -> transcribe_diarize task (not vad)
    diar_config = AppConfig(
        data_dir=tmp_path / "data_diar",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device.root_path, stable_seconds=7),
        asr_mode="diarize",
    )
    assert import_audio_files_from_port(config=diar_config, importer=RecordingFileImporter(device=device, source=source)).imported_files == 1
    conn = connect(diar_config.database_path)
    try:
        diar_tasks = fetch_all(conn, "select task_type, target_type, status from tasks")
    finally:
        conn.close()
    assert diar_tasks == [{"task_type": "transcribe_diarize", "target_type": "audio_file", "status": "pending"}]

    # chunk mode (default) still enqueues vad.
    chunk_config = AppConfig(
        data_dir=tmp_path / "data_chunk",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device.root_path, stable_seconds=7),
    )
    assert import_audio_files_from_port(config=chunk_config, importer=RecordingFileImporter(device=device, source=source)).imported_files == 1
    conn = connect(chunk_config.database_path)
    try:
        chunk_tasks = fetch_all(conn, "select task_type from tasks")
    finally:
        conn.close()
    assert [r["task_type"] for r in chunk_tasks] == ["vad"]


def test_ingest_in_diarize_mode_keeps_date_priority(tmp_path: Path) -> None:
    # The transcribe_diarize task must carry the recorded-day priority exactly like vad did
    # (date-major drain). recorded_at 2025-06-10 -> (2025-06-10 - 2000-01-01).days == 9292.
    from datetime import date

    from personal_context_node.ingest import import_audio_files_from_port
    from personal_context_node.config import DeviceDiscoveryConfig

    device = MountedDevice(device_id="dev_dji", label="DJI Mic 3", root_path=tmp_path / "mounted_dji")
    source = SourceAudioFile(
        device=device,
        source_path=device.root_path / "TX02_MIC001_20250610_173550_orig.wav",
        size_bytes=1024,
        mtime_ns=123456789,
    )
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(root_path=device.root_path, stable_seconds=7),
        asr_mode="diarize",
    )
    assert import_audio_files_from_port(config=config, importer=RecordingFileImporter(device=device, source=source)).imported_files == 1

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select priority from tasks where task_type = 'transcribe_diarize'")
    finally:
        conn.close()
    assert [r["priority"] for r in rows] == [(date(2025, 6, 10) - date(2000, 1, 1)).days]


def test_process_once_claims_transcribe_diarize_and_fans_in_when_day_complete(tmp_path: Path) -> None:
    # process_once in diarize mode claims transcribe_diarize, writes speaker-labeled segments,
    # and (once the whole day is settled) enqueues session_derive exactly once.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_1", recorded_at="2026-06-14T09:00:00+08:00")

    priority = 100
    conn = connect(config.database_path)
    try:
        enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1", priority=priority)
        conn.commit()
    finally:
        conn.close()

    asr = FakeDiarizedASRAdapter(_segments(["spk_01", "self"]))
    result = process_once(
        config=config,
        run_id="run_diar",
        vad=_vad(),
        asr=asr,
        llm=MockLLMAdapter(),
    )

    assert result.task_type == "transcribe_diarize"
    assert result.status == "succeeded"

    conn = connect(config.database_path)
    try:
        segments = fetch_all(
            conn,
            "select speaker, speaker_cluster_id, text from transcript_segments where audio_file_id = 'aud_1' and is_active = 1 order by start_ms",
        )
    finally:
        conn.close()
    assert [s["speaker"] for s in segments] == ["spk_01", "self"]
    for s in segments:
        assert s["speaker"] == s["speaker_cluster_id"]

    # The single same-day file is now settled -> session_derive enqueued exactly once for the day.
    session_tasks = [r for r in process_status_rows(config=config) if r["task_type"] == "session_derive"]
    assert len(session_tasks) == 1
    assert session_tasks[0]["target_type"] == "date_key"
    assert session_tasks[0]["target_id"] == "2026-06-14"
    assert session_tasks[0]["status"] == "pending"


def test_process_once_diarize_does_not_fan_in_until_day_complete(tmp_path: Path) -> None:
    # Two same-day files: processing the first does NOT enqueue session_derive while the second
    # is still pending (round-7 invariant under the FILE path).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_mode="diarize")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_1", recorded_at="2026-06-14T09:00:00+08:00")
    _seed_audio_file(database_path=config.database_path, audio_file_id="aud_2", recorded_at="2026-06-14T15:00:00+08:00")

    conn = connect(config.database_path)
    try:
        # aud_1 first (lower priority value claimed first via order by priority).
        enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_1", priority=1)
        enqueue_task_in_conn(conn, task_type="transcribe_diarize", target_type="audio_file", target_id="aud_2", priority=2)
        conn.commit()
    finally:
        conn.close()

    asr = FakeDiarizedASRAdapter(_segments(["self"]))
    result = process_once(config=config, run_id="run_diar_1", vad=_vad(), asr=asr, llm=MockLLMAdapter())
    assert result.task_type == "transcribe_diarize"

    # aud_2 still pending -> the day is NOT complete -> NO session_derive yet.
    session_tasks = [r for r in process_status_rows(config=config) if r["task_type"] == "session_derive"]
    assert session_tasks == []
