from __future__ import annotations

import math
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import preview_next_process_task, process_once
from personal_context_node.tasks import claim_next_task, enqueue_task, process_status_rows
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_preview_matches_claim_order_when_priority_and_availability_disagree(tmp_path: Path) -> None:
    # preview_next_process_task (the dry-run "next task" report) must order tasks IDENTICALLY to
    # claim_next_task. Otherwise `pcn process run --dry-run` reports a different task than the one
    # actually claimed whenever priority and availability disagree — exactly the date-major
    # scheduling case (earlier recorded day = lower priority value, but maybe later available_at).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        earlier = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        for task_id, priority, available_at in [
            ("t_other_day", 20300, earlier),   # later recorded day, but available sooner
            ("t_priority_day", 20260, now),     # earlier recorded day -> lower priority value
        ]:
            conn.execute(
                "insert into tasks (task_id, task_type, target_type, target_id, status, priority,"
                " available_at, created_at, updated_at) values (?, 'vad', 'audio_file', ?, 'pending', ?, ?, ?, ?)",
                (task_id, task_id, priority, available_at, now, now),
            )
        conn.commit()
    finally:
        conn.close()

    preview = preview_next_process_task(config=config)
    claimed = claim_next_task(config=config, task_type="vad", run_id="r")

    assert preview.task_id == "t_priority_day"  # priority-first, not earliest-available
    assert claimed is not None and claimed.task_id == preview.task_id  # preview agrees with the real claim


def test_process_once_runs_vad_then_asr_tasks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    vad_result = process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert vad_result.task_type == "vad"
    assert vad_result.status == "succeeded"
    tasks_after_vad = process_status_rows(config=config)
    assert any(row["task_type"] == "asr" and row["status"] == "pending" for row in tasks_after_vad)

    asr_result = process_once(
        config=config,
        run_id="run_asr",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert asr_result.task_type == "asr"
    assert asr_result.status == "succeeded"
    tasks_after_asr = process_status_rows(config=config)
    assert any(row["task_type"] == "asr" and row["status"] == "succeeded" for row in tasks_after_asr)


def test_process_once_reclaims_expired_task_before_claiming(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    task_id = next(row["task_id"] for row in process_status_rows(config=config) if row["task_type"] == "vad")
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    conn = connect(config.database_path)
    try:
        conn.execute(
            """
            update tasks
            set status = 'claimed',
                claimed_by_run_id = ?,
                claimed_at = ?,
                lease_expires_at = ?,
                updated_at = ?
            where task_id = ?
            """,
            ("crashed-run", expired_at, expired_at, expired_at, task_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = process_once(
        config=config,
        run_id="recovery-run",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert result.task_id == task_id
    assert result.task_type == "vad"
    assert result.status == "succeeded"


def test_process_once_enqueues_downstream_tasks_with_configured_max_retries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", task_max_retries=2)
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    conn = connect(config.database_path)
    try:
        rows = conn.execute("select task_type, max_retries from tasks where task_type = 'asr'").fetchall()
    finally:
        conn.close()
    assert [(row["task_type"], row["max_retries"]) for row in rows] == [("asr", 2)]


def test_process_once_runs_archive_task(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_voice_wav(source_path)
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        nas_archive_root=tmp_path / "nas" / "PersonalContext",
    )
    config.nas_archive_root.mkdir(parents=True, exist_ok=True)  # simulate a mounted NAS
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    conn = connect(config.database_path)
    try:
        conn.execute("delete from tasks")
        conn.commit()
    finally:
        conn.close()
    enqueue_task(config=config, task_type="archive", target_type="archive_scope", target_id="all")

    result = process_once(
        config=config,
        run_id="run_archive",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert result.task_type == "archive"
    assert result.status == "succeeded"
    archived_path = config.nas_archive_root / "audio" / "raw" / "2025-06-10" / source_path.name
    assert archived_path.exists()
    conn = connect(config.database_path)
    try:
        rows = conn.execute(
            """
            select af.status, ar.target_type, ar.status as archive_status, ar.verified
            from audio_files af
            join archive_records ar on ar.target_id = af.audio_file_id
            """
        ).fetchall()
    finally:
        conn.close()
    assert [(row["status"], row["target_type"], row["archive_status"], row["verified"]) for row in rows] == [
        ("archived", "audio_file", "verified", 1)
    ]


def test_process_once_archive_task_uses_configured_command_backend(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_voice_wav(source_path)
    archive_root = tmp_path / "nas" / "PersonalContext"
    marker = tmp_path / "archive-command-ran.txt"
    script = tmp_path / "copy_archive.py"
    script.write_text(
        f"""
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
marker = Path({str(marker)!r})
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, target)
marker.write_text("ran", encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    archive_root.mkdir(parents=True, exist_ok=True)  # simulate a mounted NAS
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        nas_archive_root=archive_root,
        archive_backend="command",
        archive_command=f"python3 {script} {{source_path}} {{archive_path}}",
    )
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    conn = connect(config.database_path)
    try:
        conn.execute("delete from tasks")
        conn.commit()
    finally:
        conn.close()
    enqueue_task(config=config, task_type="archive", target_type="archive_scope", target_id="all")

    result = process_once(
        config=config,
        run_id="run_archive_command",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert result.task_type == "archive"
    assert result.status == "succeeded"
    assert (archive_root / "audio" / "raw" / "2025-06-10" / source_path.name).read_bytes() == source_path.read_bytes()
    assert marker.read_text(encoding="utf-8") == "ran"


def _write_voice_wav(path: Path, seconds: float = 0.7, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(seconds * sample_rate)):
            sample = int(10_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))


def test_process_once_prefers_finishing_a_day_over_more_asr(tmp_path: Path) -> None:
    # Regression guard: after the PROCESS_TASK_ORDER reorder, a claimable session_derive
    # (day A fully transcribed) must be picked before a pending asr (day B still transcribing).
    from personal_context_node.storage.sqlite import initialize

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        # day A: seed a claimable session_derive task
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, available_at, created_at, updated_at)
            values ('task_sd_dayA', 'session_derive', 'date_key', '2026-06-01', 'pending', ?, ?, ?)
            """,
            (now, now, now),
        )
        # day B: seed a pending asr task (a chunk still to transcribe)
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, available_at, created_at, updated_at)
            values ('task_asr_dayB', 'asr', 'audio_chunk', 'chk_dayB_001', 'pending', ?, ?, ?)
            """,
            (now, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    result = process_once(
        config=config,
        run_id="r_test_order",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="test"),
    )

    # The reordered PROCESS_TASK_ORDER must prefer session_derive over asr.
    assert result.task_type == "session_derive", (
        f"expected session_derive to preempt asr, got {result.task_type}"
    )


def test_terminal_failure_completing_fanin_still_enqueues_downstream(tmp_path: Path) -> None:
    # Liveness regression: a terminal task failure that COMPLETES a fan-in set must
    # still register the downstream (the fan-in is otherwise only evaluated on success),
    # else the pipeline silently deadlocks (§25.4 rule 3).
    import math
    import wave

    from personal_context_node.adapters.asr.mock import MockASRAdapter
    from personal_context_node.adapters.vad.energy import EnergyVadAdapter
    from personal_context_node.core.ports.errors import TerminalPortError
    from personal_context_node.pipeline import run_first_milestone

    class _TerminalASR:
        def transcribe(self, path):
            raise TerminalPortError("unsupported format")

    source = tmp_path / "source"
    source.mkdir()
    wav = source / "TX02_MIC001_20870510_173550_orig.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        frames = bytearray()
        for index in range(int(0.7 * 16000)):
            frames.extend(int(10000 * math.sin(2 * math.pi * 440 * index / 16000)).to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(frames))

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    vad = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150)
    process_once(config=config, run_id="vad", vad=vad, asr=MockASRAdapter(text="x"), max_chunk_ms=1000)
    try:
        process_once(config=config, run_id="asr", vad=vad, asr=_TerminalASR(), max_chunk_ms=1000)
    except TerminalPortError:
        pass

    conn = connect(config.database_path)
    try:
        task_types = {row["task_type"] for row in fetch_all(conn, "select task_type from tasks")}
    finally:
        conn.close()
    assert "session_derive" in task_types  # downstream registered despite terminal asr
