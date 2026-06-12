from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.system_summary import daily_system_summary


def test_daily_system_summary_counts_jobs_tasks_and_archives(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary_inputs(config)

    summary = daily_system_summary(config=config, day="2087-05-10")

    assert summary.day == "2087-05-10"
    assert summary.jobs_total == 2
    assert summary.jobs_succeeded == 1
    assert summary.jobs_failed == 1
    assert summary.tasks_pending == 1
    assert summary.tasks_failed == 2
    assert summary.archived_records == 1
    assert summary.audio_files_imported == 1
    assert summary.transcript_segments == 2
    assert summary.memory_candidates == 1
    assert summary.signed_events == 1


def test_system_summary_cli_prints_daily_operational_summary(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary_inputs(config)

    result = CliRunner().invoke(
        app,
        [
            "system-summary",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "day=2087-05-10" in result.output
    assert "jobs_total=2" in result.output
    assert "jobs_failed=1" in result.output
    assert "tasks_pending=1" in result.output
    assert "tasks_failed=2" in result.output
    assert "archived_records=1" in result.output
    assert "audio_files_imported=1" in result.output
    assert "transcript_segments=2" in result.output
    assert "memory_candidates=1" in result.output
    assert "signed_events=1" in result.output


def test_system_summary_cli_uses_config_path(tmp_path) -> None:
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\n", encoding="utf-8")
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    _insert_summary_inputs(config)

    result = CliRunner().invoke(app, ["system-summary", "--config", str(config_path), "--day", "2087-05-10"])

    assert result.exit_code == 0, result.output
    assert "day=2087-05-10" in result.output
    assert "jobs_total=2" in result.output
    assert "signed_events=1" in result.output


def test_daily_system_summary_counts_audio_files_by_recorded_day_not_import_day(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
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
                "aud_late_import",
                "DJI Mic 3",
                "/source-late.wav",
                "/local-late.wav",
                "sha256:late",
                1000,
                "2087-05-10T07:30:00+08:00",
                "2087-05-12T07:31:00+08:00",
                "imported",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    summary = daily_system_summary(config=config, day="2087-05-10")

    assert summary.audio_files_imported == 1


def _insert_summary_inputs(config: AppConfig) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at, finished_at, error) values (?, ?, ?, ?, ?, ?)",
            ("run_ok", "process-run", "succeeded", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:01+08:00", None),
        )
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at, finished_at, error) values (?, ?, ?, ?, ?, ?)",
            ("run_failed", "process-run", "failed", "2087-05-10T09:00:00+08:00", "2087-05-10T09:00:02+08:00", "boom"),
        )
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at, finished_at, error) values (?, ?, ?, ?, ?, ?)",
            ("run_other_day", "process-run", "failed", "2087-05-11T09:00:00+08:00", "2087-05-11T09:00:02+08:00", "boom"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, created_at, updated_at, available_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("task_pending", "vad", "audio_file", "aud_1", "pending", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, created_at, updated_at, available_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("task_retry", "asr", "audio_chunk", "chk_1", "failed_retryable", "2087-05-10T08:00:00+08:00", "2087-05-10T08:05:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, created_at, updated_at, available_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("task_terminal", "asr", "audio_chunk", "chk_2", "failed_terminal", "2087-05-10T08:00:00+08:00", "2087-05-10T08:05:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            """
            insert into archive_records (
              archive_record_id, target_type, target_id, source_path, archive_path, sha256,
              status, verified, archived_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "arc_1",
                "audio_file",
                "aud_1",
                "/tmp/source.wav",
                "/tmp/archive.wav",
                "abc",
                "verified",
                1,
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_1",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:audio",
                1000,
                "2087-05-10T07:30:00+08:00",
                "2087-05-10T07:31:00+08:00",
                "imported",
            ),
        )
        for segment_id in ["seg_1", "seg_2"]:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_1",
                    f"chk_{segment_id}",
                    0,
                    1000,
                    "系统摘要需要覆盖本地处理产物。",
                    "zh",
                    "self",
                    f"ev_{segment_id}",
                    0.99,
                    "mock",
                    "mock-asr",
                    "test",
                ),
            )
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, source_type, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, date_key, prompt_version,
              created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cand_1",
                "llm_daily_context",
                "系统摘要需要覆盖本地处理产物。",
                "requirement",
                '{"id":"personal_context_node","label":"Personal Context Node","type":"project"}',
                0.9,
                '["ev_seg_1"]',
                "pending_review",
                "2087-05-10",
                "llm_port.candidate_extraction.v1",
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:00:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into signed_events (
              event_hash, event_type, signer_did, object_id, owner_id, owner_sequence,
              prev_event_hash, created_at, payload_json, raw_event_json,
              signing_body_json, canonical_signing_body_hash, trust_status,
              signature, public_key, verified
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evhash_1",
                "memory_card.created",
                "did:key:test",
                "mem_1",
                "did:key:test",
                1,
                None,
                "2087-05-10T08:10:00+08:00",
                "{}",
                "{}",
                "{}",
                "sha256:event",
                "trusted",
                "sig",
                "pub",
                1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
