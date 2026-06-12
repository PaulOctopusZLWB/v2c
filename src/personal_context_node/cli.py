from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import typer

from personal_context_node.adapters.archive.local_filesystem import LocalFilesystemArchiveAdapter
from personal_context_node.adapters.asr.command import CommandASRAdapter
from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.file_import.local_directory import LocalDirectoryFileImportAdapter
from personal_context_node.adapters.llm.command import CommandLLMAdapter
from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.adapters.vad.command import CommandVADAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.archive import (
    archive_completed_audio,
    archive_status_rows,
    cleanup_archived_audio,
    mark_cleanup_eligible_audio,
)
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.daily_reports import get_daily_report_status
from personal_context_node.doctor import run_doctor
from personal_context_node.jobs import job_status_rows, record_job_run
from personal_context_node.init_health import check_health, initialize_workspace
from personal_context_node.ingest import (
    import_audio_files,
    import_audio_files_from_port,
    repair_bwf_metadata_in_source_dir,
    scan_audio_files,
)
from personal_context_node.launchd import install_launchd_plists, uninstall_launchd_plists, write_launchd_plists
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.memory_export import export_memory_events
from personal_context_node.memory_import import import_memory_events
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.obsidian_publish import publish_obsidian_day
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.obsidian_sessions import publish_session_notes
from personal_context_node.pipeline import run_first_milestone as run_first_milestone_pipeline
from personal_context_node.process_runner import preview_next_process_task, process_once
from personal_context_node.speaker_review import publish_speaker_review, sync_speaker_review
from personal_context_node.system_summary import daily_system_summary
from personal_context_node.tasks import process_status_rows, rerun_task, retry_task
from personal_context_node.transcription import transcribe_pending_chunks


app = typer.Typer(help="Personal Context Node local pipeline.")
ingest_app = typer.Typer(help="Audio ingest commands.")
app.add_typer(ingest_app, name="ingest")
process_app = typer.Typer(help="Task processing commands.")
app.add_typer(process_app, name="process")
obsidian_app = typer.Typer(help="Obsidian publish and review commands.")
app.add_typer(obsidian_app, name="obsidian")
memory_app = typer.Typer(help="Memory protocol commands.")
app.add_typer(memory_app, name="memory")
archive_app = typer.Typer(help="Archive commands.")
app.add_typer(archive_app, name="archive")


@app.callback()
def main() -> None:
    """Run local-first Personal Context Node jobs."""


@app.command(name="init")
def init_cmd(
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    config_path: Path | None = typer.Option(None, help="Optional TOML config path to create if missing."),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = initialize_workspace(config=config, config_path=config_path)
    typer.echo(
        " ".join(
            [
                f"initialized={result.initialized}",
                f"data_dir={config.data_dir}",
                f"obsidian_vault={config.obsidian_vault}",
                f"config_path={result.config_path or ''}",
            ]
        )
    )


@app.command(name="health")
def health_cmd(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = check_health(config=config)
    typer.echo(
        " ".join(
            [
                f"status={result.status}",
                f"database={result.database}",
                f"obsidian_vault={result.obsidian_vault}",
            ]
        )
    )


@app.command(name="doctor")
def doctor_cmd(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    source_dir: Path | None = typer.Option(None, help="Optional recording source directory to check."),
    archive_root: Path | None = typer.Option(None, help="Optional archive root to check."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = run_doctor(config=config, source_dir=source_dir, archive_root=archive_root)
    typer.echo(
        " ".join(
            [
                f"status={result.status}",
                f"database={result.database}",
                f"obsidian_vault={result.obsidian_vault}",
                f"source_dir={result.source_dir}",
                f"archive_root={result.archive_root}",
                f"pending_tasks={result.pending_tasks}",
                f"failed_tasks={result.failed_tasks}",
                f"recent_failed_jobs={result.recent_failed_jobs}",
                f"memory_invalid_events={result.memory_invalid_events}",
                f"memory_materialization_mismatches={result.memory_materialization_mismatches}",
            ]
        )
    )


@app.command()
def run_first_milestone(
    source_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Directory containing WAV recordings."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    confirm_first_candidate: bool = typer.Option(False, help="Confirm the first generated candidate for smoke tests."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = run_first_milestone_pipeline(
        config=config,
        source_dir=source_dir,
        confirm_first_candidate=confirm_first_candidate,
    )
    typer.echo(
        " ".join(
            [
                f"imported_files={result.imported_files}",
                f"transcript_segments={result.transcript_segments}",
                f"memory_candidates={result.memory_candidates}",
                f"signed_events={result.signed_events}",
            ]
        )
    )


@app.command(name="ingest-scan")
def ingest_scan(
    source_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Directory containing WAV recordings."),
) -> None:
    _ingest_scan(source_dir=source_dir)


@ingest_app.command(name="scan")
def ingest_scan_group(
    source_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Directory containing WAV recordings."),
) -> None:
    _ingest_scan(source_dir=source_dir)


def _ingest_scan(*, source_dir: Path) -> None:
    result = scan_audio_files(source_dir=source_dir)
    typer.echo(f"files_found={len(result.files)}")
    for path in result.files:
        typer.echo(str(path))


@app.command(name="ingest-import")
def ingest_import(
    source_dir: Path | None = typer.Option(None, exists=True, file_okay=False, help="Directory containing WAV recordings."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _ingest_import(source_dir=source_dir, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


@ingest_app.command(name="import")
def ingest_import_group(
    source_dir: Path | None = typer.Option(None, exists=True, file_okay=False, help="Directory containing WAV recordings."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _ingest_import(source_dir=source_dir, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


def _ingest_import(
    *,
    source_dir: Path | None,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    if source_dir is not None:
        result = import_audio_files(config=config, source_dir=source_dir)
    elif not config.dji_mic_3.enabled:
        importer = LocalDirectoryFileImportAdapter(device_roots=[], device_label=config.source_device)
        result = import_audio_files_from_port(config=config, importer=importer)
    elif config.dji_mic_3.root_path is not None:
        importer = LocalDirectoryFileImportAdapter(
            device_roots=[config.dji_mic_3.root_path],
            device_label=config.source_device,
            audio_globs=config.dji_mic_3.audio_globs,
            volume_name_patterns=config.dji_mic_3.volume_name_patterns,
        )
        result = import_audio_files_from_port(config=config, importer=importer)
    else:
        raise typer.BadParameter("--source-dir is required when [device.dji_mic_3].root_path is not configured")
    typer.echo(f"imported_files={result.imported_files}")


@ingest_app.command(name="fix-metadata")
def ingest_fix_metadata(
    source_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Directory containing WAV recordings."),
    recursive: bool = typer.Option(False, help="Scan nested subdirectories for WAV files."),
    dry_run: bool = typer.Option(False, help="Report files that need fixing without writing changes."),
) -> None:
    result = repair_bwf_metadata_in_source_dir(source_dir=source_dir, recursive=recursive, dry_run=dry_run)
    typer.echo(
        " ".join(
            [
                f"scanned_files={result.scanned_files}",
                f"repaired_files={result.repaired_files}",
                f"skipped_files={result.skipped_files}",
            ]
        )
    )


@app.command()
def preprocess(
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    vad_threshold: float | None = typer.Option(None, min=0.0, max=1.0, help="Energy VAD RMS threshold."),
    vad_backend: str | None = typer.Option(None, help="VAD backend: energy, mock, command, or funasr."),
    vad_command: str | None = typer.Option(None, help="Command VAD wrapper."),
    max_chunk_ms: int | None = typer.Option(None, min=100, help="Maximum ASR chunk duration in milliseconds."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    resolved_vad_backend = vad_backend or config.vad_backend
    vad = _build_vad(
        vad_backend=resolved_vad_backend,
        vad_command=vad_command,
        vad_threshold=vad_threshold if vad_threshold is not None else config.vad_threshold,
        merge_gap_ms=config.merge_gap_ms,
        min_speech_ms=config.min_speech_ms,
    )
    result = preprocess_imported_audio(
        config=config,
        vad=vad,
        max_chunk_ms=max_chunk_ms or config.max_chunk_ms,
        chunk_overlap_ms=config.chunk_overlap_ms if config_path else 0,
    )
    typer.echo(
        " ".join(
            [
                f"audio_files_processed={result.audio_files_processed}",
                f"speech_ranges_created={result.speech_ranges_created}",
                f"audio_chunks_created={result.audio_chunks_created}",
            ]
        )
    )


@app.command()
def transcribe(
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    mock_text: str | None = typer.Option(None, help="Text emitted by the mock ASR adapter."),
    asr_backend: str | None = typer.Option(None, help="ASR backend: mock, command, or funasr."),
    asr_command: str | None = typer.Option(None, help="Command ASR wrapper, e.g. 'python scripts/funasr_sensevoice_wrapper.py'."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    asr = _build_asr(
        asr_backend=asr_backend or config.asr_backend,
        asr_command=asr_command or config.asr_command,
        mock_text=mock_text,
        language=config.asr_language,
        model_name=config.asr_model_name,
    )
    result = transcribe_pending_chunks(config=config, asr=asr)
    typer.echo(
        " ".join(
            [
                f"chunks_transcribed={result.chunks_transcribed}",
                f"segments_created={result.segments_created}",
            ]
        )
    )


@app.command()
def summarize(
    day: str = typer.Option(..., help="Recording day in YYYY-MM-DD format."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    llm_backend: str | None = typer.Option(None, help="LLM backend: rule_based, mock, or command."),
    llm_command: str | None = typer.Option(None, help="Command LLM wrapper."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    llm = _build_llm(llm_backend=llm_backend or config.llm_backend, llm_command=llm_command or config.llm_command)
    result = generate_daily_context(config=config, day=day, llm=llm)
    typer.echo(
        " ".join(
            [
                f"summaries_created={result.summaries_created}",
                f"memory_candidates_created={result.memory_candidates_created}",
            ]
        )
    )


@app.command()
def publish_review(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    review_path = publish_candidate_review(config=config, day=day)
    typer.echo(f"review_path={review_path}")


@obsidian_app.command(name="publish")
def obsidian_publish_group(
    date: str = typer.Option(..., "--date", help="Publish date in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = publish_obsidian_day(config=config, day=date)
    typer.echo(
        " ".join(
            [
                f"daily_notes_written={result.daily_notes_written}",
                f"session_notes_written={result.session_notes_written}",
                f"candidate_review_written={result.candidate_review_written}",
                f"speaker_review_written={result.speaker_review_written}",
                f"confirmed_memory_written={result.confirmed_memory_written}",
            ]
        )
    )


@app.command(name="publish-session-notes")
def publish_session_notes_cmd(
    day: str = typer.Option(..., help="Session day in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = publish_session_notes(config=config, day=day)
    typer.echo(f"notes_written={result.notes_written}")


@app.command()
def confirm_review(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _sync_candidate_review(day=day, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


@obsidian_app.command(name="sync-review")
def obsidian_sync_review_group(
    date: str = typer.Option(..., "--date", help="Review date in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _confirm_sync_reviews(day=date, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


def _sync_candidate_review(
    *,
    day: str,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = confirm_checked_candidates(config=config, day=day)
    typer.echo(
        " ".join(
            [
                f"candidates_confirmed={result.candidates_confirmed}",
                f"signed_events_created={result.signed_events_created}",
            ]
        )
    )


def _confirm_sync_reviews(
    *,
    day: str,
    data_dir: Path | None,
    obsidian_vault: Path | None,
    config_path: Path | None = None,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    candidate_result = confirm_checked_candidates(config=config, day=day)
    speaker_result = sync_speaker_review(config=config, day=day)
    typer.echo(
        " ".join(
            [
                f"candidates_confirmed={candidate_result.candidates_confirmed}",
                f"signed_events_created={candidate_result.signed_events_created}",
                f"speaker_mappings_upserted={speaker_result.mappings_upserted}",
                f"segment_overrides_upserted={speaker_result.segment_overrides_upserted}",
            ]
        )
    )


@app.command(name="publish-speaker-review")
def publish_speaker_review_cmd(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    review_path = publish_speaker_review(config=config, day=day)
    typer.echo(f"review_path={review_path}")


@app.command(name="sync-speaker-review")
def sync_speaker_review_cmd(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = sync_speaker_review(config=config, day=day)
    typer.echo(
        " ".join(
            [
                f"mappings_upserted={result.mappings_upserted}",
                f"segment_overrides_upserted={result.segment_overrides_upserted}",
            ]
        )
    )


@archive_app.callback(invoke_without_command=True)
def archive(
    ctx: typer.Context,
    archive_root: Path | None = typer.Option(None, help="NAS or local archive root."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    require_existing_root: bool = typer.Option(False, help="Treat a missing archive root as unavailable."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _archive_run(
        archive_root=archive_root,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
        require_existing_root=require_existing_root,
    )


@archive_app.command(name="run")
def archive_run_group(
    archive_root: Path | None = typer.Option(None, help="NAS or local archive root."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    require_existing_root: bool = typer.Option(False, help="Treat a missing archive root as unavailable."),
) -> None:
    _archive_run(
        archive_root=archive_root,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
        require_existing_root=require_existing_root,
    )


@archive_app.command(name="cleanup")
def archive_cleanup_group(
    archived_before: str = typer.Option(..., help="Only clean audio archived before this ISO-8601 timestamp."),
    archive_root: Path | None = typer.Option(None, help="NAS or local archive root."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    archive_target = archive_root or config.nas_archive_root
    result = cleanup_archived_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_target),
        archived_before=_parse_iso_datetime(archived_before),
    )
    typer.echo(f"files_removed={result.files_removed} files_pending={result.files_pending}")


@archive_app.command(name="mark-cleanup-eligible")
def archive_mark_cleanup_eligible_group(
    archived_before: str = typer.Option(..., help="Only mark audio archived before this ISO-8601 timestamp."),
    archive_root: Path | None = typer.Option(None, help="NAS or local archive root."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    archive_target = archive_root or config.nas_archive_root
    result = mark_cleanup_eligible_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_target),
        archived_before=_parse_iso_datetime(archived_before),
    )
    typer.echo(f"files_marked={result.files_marked} files_pending={result.files_pending}")


@app.command(name="archive-status")
def archive_status(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    limit: int = typer.Option(20, min=1, help="Maximum rows to print."),
) -> None:
    _archive_status(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault, limit=limit)


@archive_app.command(name="status")
def archive_status_group(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    limit: int = typer.Option(20, min=1, help="Maximum rows to print."),
) -> None:
    _archive_status(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault, limit=limit)


def _archive_status(*, config_path: Path | None, data_dir: Path | None, obsidian_vault: Path | None, limit: int) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    for row in archive_status_rows(config=config, limit=limit):
        typer.echo(
            " ".join(
                [
                    f"archive_record_id={row['archive_record_id']}",
                    f"target_type={row['target_type']}",
                    f"target_id={row['target_id']}",
                    f"status={row['status']}",
                    f"verified={row['verified']}",
                    f"archived_at={row['archived_at']}",
                    f"archive_path={row['archive_path']}",
                    f"last_error={row['last_error'] or ''}",
                ]
            )
        )


def _archive_run(
    *,
    archive_root: Path | None,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
    require_existing_root: bool,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    archive_target = archive_root or config.nas_archive_root
    result = archive_completed_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_target, require_existing_root=require_existing_root),
    )
    typer.echo(
        " ".join(
            [
                f"files_archived={result.files_archived}",
                f"files_pending={result.files_pending}",
                f"events_archived={result.events_archived}",
                f"events_pending={result.events_pending}",
                f"transcripts_archived={result.transcripts_archived}",
                f"transcripts_pending={result.transcripts_pending}",
                f"summaries_archived={result.summaries_archived}",
                f"summaries_pending={result.summaries_pending}",
                f"memory_candidates_archived={result.memory_candidates_archived}",
                f"memory_candidates_pending={result.memory_candidates_pending}",
            ]
        )
    )


def _parse_iso_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("--archived-before must be an ISO-8601 datetime") from exc


def _load_config(
    *,
    config_path: Path | None,
    data_dir: Path | None = None,
    obsidian_vault: Path | None = None,
) -> AppConfig:
    if config_path:
        return AppConfig.from_toml(config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    return AppConfig(
        data_dir=data_dir or Path("data"),
        obsidian_vault=obsidian_vault or Path("/Users/paul/Documents/Obsidian/PersonalContext"),
    )


@app.command(name="launchd-write-plists")
def launchd_write_plists(
    output_dir: Path = typer.Option(Path("build/launchd"), help="Directory to write plist templates."),
    working_directory: Path = typer.Option(Path.cwd(), help="Repository working directory."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    source_dir: Path | None = typer.Option(None, help="Mounted DJI source directory."),
    archive_root: Path | None = typer.Option(None, help="NAS archive root."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    resolved_source_dir = source_dir or config.dji_mic_3.root_path or Path("/Volumes/DJI")
    resolved_archive_root = archive_root or config.nas_archive_root
    paths = write_launchd_plists(
        output_dir=output_dir,
        working_directory=str(working_directory),
        data_dir=str(config.data_dir),
        obsidian_vault=str(config.obsidian_vault),
        source_dir=str(resolved_source_dir),
        archive_root=str(resolved_archive_root),
        dry_run=True,
    )
    typer.echo(f"plists_written={len(paths)} output_dir={output_dir}")


@app.command(name="launchd-install")
def launchd_install(
    plist_dir: Path = typer.Option(Path("build/launchd"), help="Directory containing generated plist templates."),
    launch_agents_dir: Path = typer.Option(
        Path.home() / "Library" / "LaunchAgents",
        help="User LaunchAgents directory.",
    ),
    uid: int | None = typer.Option(None, help="macOS user id. Defaults to current process uid."),
    execute: bool = typer.Option(False, help="Actually copy files and call launchctl."),
) -> None:
    plist_paths = sorted(plist_dir.glob("com.personal-context-node.*.plist"))
    result = install_launchd_plists(
        plist_paths=plist_paths,
        launch_agents_dir=launch_agents_dir,
        uid=uid,
        dry_run=not execute,
    )
    typer.echo(f"launchd_install dry_run={not execute} plists={len(result.installed_paths)}")
    for command in result.commands:
        typer.echo(" ".join(command))


@app.command(name="launchd-uninstall")
def launchd_uninstall(
    launch_agents_dir: Path = typer.Option(
        Path.home() / "Library" / "LaunchAgents",
        help="User LaunchAgents directory.",
    ),
    uid: int | None = typer.Option(None, help="macOS user id. Defaults to current process uid."),
    execute: bool = typer.Option(False, help="Actually call launchctl and remove files."),
) -> None:
    labels = [
        "com.personal-context-node.ingest",
        "com.personal-context-node.process",
        "com.personal-context-node.daily",
        "com.personal-context-node.archive",
    ]
    result = uninstall_launchd_plists(
        labels=labels,
        launch_agents_dir=launch_agents_dir,
        uid=uid,
        dry_run=not execute,
    )
    typer.echo(f"launchd_uninstall dry_run={not execute} plists={len(result.removed_paths)}")
    for command in result.commands:
        typer.echo(" ".join(command))


@app.command(name="memory-verify")
def memory_verify(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _memory_verify(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


@memory_app.command(name="verify")
def memory_verify_group(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _memory_verify(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


def _memory_verify(*, config_path: Path | None, data_dir: Path | None, obsidian_vault: Path | None) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    job_run = record_job_run(
        config=config,
        job_name="memory-verify",
        operation=lambda: verify_memory_events(config=config),
    )
    result = job_run.result
    typer.echo(
        " ".join(
            [
                f"total_events={result.total_events}",
                f"valid_events={result.valid_events}",
                f"invalid_events={result.invalid_events}",
                f"materialization_mismatches={result.materialization_mismatches}",
            ]
        )
    )
    if result.invalid_events or result.materialization_mismatches:
        raise typer.Exit(code=1)


@app.command(name="memory-export")
def memory_export(
    since: str = typer.Option(..., help="Inclusive created_at lower bound, e.g. 2026-06-01."),
    output_path: Path = typer.Option(Path("build/memory-events.jsonl"), help="JSONL export path."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _memory_export(
        since=since,
        output_path=output_path,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
    )


@memory_app.command(name="export")
def memory_export_group(
    since: str = typer.Option(..., help="Inclusive created_at lower bound, e.g. 2026-06-01."),
    output_path: Path = typer.Option(Path("build/memory-events.jsonl"), help="JSONL export path."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _memory_export(
        since=since,
        output_path=output_path,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
    )


@app.command(name="memory-import")
def memory_import(
    input_path: Path = typer.Option(..., exists=True, dir_okay=False, help="JSONL signed event import path."),
    public_key: str = typer.Option(..., help="Base64url Ed25519 public key for verifying imported events."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _memory_import(
        input_path=input_path,
        public_key=public_key,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
    )


@memory_app.command(name="import")
def memory_import_group(
    input_path: Path = typer.Option(..., exists=True, dir_okay=False, help="JSONL signed event import path."),
    public_key: str = typer.Option(..., help="Base64url Ed25519 public key for verifying imported events."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _memory_import(
        input_path=input_path,
        public_key=public_key,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
    )


@memory_app.command(name="confirm-sync")
def memory_confirm_sync_group(
    date: str = typer.Option(..., "--date", help="Review date in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _confirm_sync_reviews(day=date, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


def _memory_export(
    *,
    since: str,
    output_path: Path,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = export_memory_events(config=config, output_path=output_path, since=since)
    typer.echo(f"events_exported={result.events_exported} output_path={result.output_path}")


def _memory_import(
    *,
    input_path: Path,
    public_key: str,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = import_memory_events(config=config, input_path=input_path, public_key=public_key)
    typer.echo(
        " ".join(
            [
                f"events_imported={result.events_imported}",
                f"trusted_events={result.trusted_events}",
                f"rejected_events={result.rejected_events}",
                f"unsupported_events={result.unsupported_events}",
            ]
        )
    )


@app.command(name="job-status")
def job_status(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    limit: int = typer.Option(20, min=1, help="Maximum rows to print."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    rows = job_status_rows(config=config, limit=limit)
    for row in rows:
        typer.echo(
            " ".join(
                [
                    f"run_id={row['run_id']}",
                    f"job_name={row['job_name']}",
                    f"status={row['status']}",
                    f"duration_ms={row['duration_ms'] if row['duration_ms'] is not None else ''}",
                    f"error={row['error'] or ''}",
                ]
            )
        )


@app.command(name="system-summary")
def system_summary(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    day: str = typer.Option(..., help="Day to summarize, formatted as YYYY-MM-DD."),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    summary = daily_system_summary(config=config, day=day)
    typer.echo(
        " ".join(
            [
                f"day={summary.day}",
                f"jobs_total={summary.jobs_total}",
                f"jobs_succeeded={summary.jobs_succeeded}",
                f"jobs_failed={summary.jobs_failed}",
                f"tasks_pending={summary.tasks_pending}",
                f"tasks_failed={summary.tasks_failed}",
                f"archived_records={summary.archived_records}",
                f"audio_files_imported={summary.audio_files_imported}",
                f"transcript_segments={summary.transcript_segments}",
                f"memory_candidates={summary.memory_candidates}",
                f"signed_events={summary.signed_events}",
            ]
        )
    )


@app.command(name="process-status")
def process_status(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _process_status(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


@process_app.command(name="status")
def process_status_group(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _process_status(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


def _process_status(*, config_path: Path | None, data_dir: Path | None, obsidian_vault: Path | None) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    for row in process_status_rows(config=config):
        typer.echo(
            " ".join(
                [
                    f"task_id={row['task_id']}",
                    f"task_type={row['task_type']}",
                    f"target_type={row['target_type']}",
                    f"target_id={row['target_id']}",
                    f"status={row['status']}",
                    f"attempt_count={row['attempt_count']}",
                    f"last_error={row['last_error'] or ''}",
                    f"duration_ms={row['duration_ms'] if row['duration_ms'] is not None else ''}",
                    f"model_name={row['model_name'] or ''}",
                    f"model_version={row['model_version'] or ''}",
                ]
            )
        )


@app.command(name="process-retry")
def process_retry(
    task_id: str = typer.Option(..., help="Task id to reset to pending."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _process_retry(task_id=task_id, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


@process_app.command(name="retry")
def process_retry_group(
    task_id: str = typer.Option(..., help="Task id to reset to pending."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _process_retry(task_id=task_id, config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)


def _process_retry(*, task_id: str, config_path: Path | None, data_dir: Path | None, obsidian_vault: Path | None) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = retry_task(config=config, task_id=task_id)
    typer.echo(f"task_id={result.task_id} status={result.status}")


@app.command(name="process-rerun")
def process_rerun(
    task_type: str = typer.Option(..., help="Task type to rerun, e.g. asr."),
    target_type: str = typer.Option(..., help="Task target type, e.g. audio_chunk."),
    target_id: str = typer.Option(..., help="Task target id."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _process_rerun(
        task_type=task_type,
        target_type=target_type,
        target_id=target_id,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
    )


@process_app.command(name="rerun")
def process_rerun_group(
    task_type: str = typer.Option(..., help="Task type to rerun, e.g. asr."),
    target_type: str = typer.Option(..., help="Task target type, e.g. audio_chunk."),
    target_id: str = typer.Option(..., help="Task target id."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    _process_rerun(
        task_type=task_type,
        target_type=target_type,
        target_id=target_id,
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
    )


def _process_rerun(
    *,
    task_type: str,
    target_type: str,
    target_id: str,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = rerun_task(config=config, task_type=task_type, target_type=target_type, target_id=target_id)
    typer.echo(f"task_id={result.task_id} created={result.created} status=pending")


@app.command(name="process-run")
def process_run(
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    vad_threshold: float | None = typer.Option(None, min=0.0, max=1.0, help="Energy VAD RMS threshold."),
    vad_backend: str | None = typer.Option(None, help="VAD backend: energy, mock, command, or funasr."),
    vad_command: str | None = typer.Option(None, help="Command VAD wrapper."),
    max_chunk_ms: int | None = typer.Option(None, min=100, help="Maximum ASR chunk duration in milliseconds."),
    asr_backend: str | None = typer.Option(None, help="ASR backend: mock, command, or funasr."),
    asr_command: str | None = typer.Option(None, help="Command ASR wrapper."),
    llm_backend: str | None = typer.Option(None, help="LLM backend: rule_based, mock, or command."),
    llm_command: str | None = typer.Option(None, help="Command LLM wrapper."),
    mock_text: str | None = typer.Option(None, help="Text emitted by mock ASR."),
    mock: bool = typer.Option(False, "--mock", help="Explicitly use mock VAD, ASR, and LLM backends."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the next runnable task without mutating state."),
) -> None:
    _process_run(
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
        vad_threshold=vad_threshold,
        vad_backend="mock" if mock and vad_backend is None else vad_backend,
        vad_command=vad_command,
        max_chunk_ms=max_chunk_ms,
        asr_backend="mock" if mock else asr_backend,
        asr_command=asr_command,
        llm_backend="mock" if mock and llm_backend is None else llm_backend,
        llm_command=llm_command,
        mock_text=mock_text,
        dry_run=dry_run,
    )


@process_app.command(name="run")
def process_run_group(
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    vad_threshold: float | None = typer.Option(None, min=0.0, max=1.0, help="Energy VAD RMS threshold."),
    vad_backend: str | None = typer.Option(None, help="VAD backend: energy, mock, command, or funasr."),
    vad_command: str | None = typer.Option(None, help="Command VAD wrapper."),
    max_chunk_ms: int | None = typer.Option(None, min=100, help="Maximum ASR chunk duration in milliseconds."),
    asr_backend: str | None = typer.Option(None, help="ASR backend: mock, command, or funasr."),
    asr_command: str | None = typer.Option(None, help="Command ASR wrapper."),
    llm_backend: str | None = typer.Option(None, help="LLM backend: rule_based, mock, or command."),
    llm_command: str | None = typer.Option(None, help="Command LLM wrapper."),
    mock_text: str | None = typer.Option(None, help="Text emitted by mock ASR."),
    mock: bool = typer.Option(False, "--mock", help="Explicitly use mock VAD, ASR, and LLM backends."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the next runnable task without mutating state."),
) -> None:
    _process_run(
        config_path=config_path,
        data_dir=data_dir,
        obsidian_vault=obsidian_vault,
        vad_threshold=vad_threshold,
        vad_backend="mock" if mock and vad_backend is None else vad_backend,
        vad_command=vad_command,
        max_chunk_ms=max_chunk_ms,
        asr_backend="mock" if mock else asr_backend,
        asr_command=asr_command,
        llm_backend="mock" if mock and llm_backend is None else llm_backend,
        llm_command=llm_command,
        mock_text=mock_text,
        dry_run=dry_run,
    )


def _process_run(
    *,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
    vad_threshold: float | None,
    vad_backend: str | None,
    vad_command: str | None,
    max_chunk_ms: int | None,
    asr_backend: str | None,
    asr_command: str | None,
    llm_backend: str | None,
    llm_command: str | None,
    mock_text: str | None,
    dry_run: bool,
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    if dry_run:
        result = preview_next_process_task(config=config)
        typer.echo(
            " ".join(
                [
                    f"task_id={result.task_id or ''}",
                    f"task_type={result.task_type or ''}",
                    f"status={result.status}",
                ]
            )
        )
        return
    vad = _build_vad(
        vad_backend=vad_backend or config.vad_backend,
        vad_command=vad_command,
        vad_threshold=config.vad_threshold if vad_threshold is None else vad_threshold,
        merge_gap_ms=config.merge_gap_ms,
        min_speech_ms=config.min_speech_ms,
    )
    asr = _build_asr(
        asr_backend=asr_backend or config.asr_backend,
        asr_command=asr_command or config.asr_command,
        mock_text=mock_text,
        language=config.asr_language,
        model_name=config.asr_model_name,
    )
    llm = _build_llm(llm_backend=llm_backend or config.llm_backend, llm_command=llm_command or config.llm_command)
    run_id = f"run_{uuid4().hex}"
    result = record_job_run(
        config=config,
        job_name="process-run",
        run_id=run_id,
        operation=lambda: process_once(
            config=config,
            run_id=run_id,
            vad=vad,
            asr=asr,
            llm=llm,
            max_chunk_ms=max_chunk_ms or config.max_chunk_ms,
        ),
    ).result
    typer.echo(
        " ".join(
            [
                f"task_id={result.task_id or ''}",
                f"task_type={result.task_type or ''}",
                f"status={result.status}",
            ]
        )
    )


def _build_vad(
    *,
    vad_backend: str,
    vad_command: str | None,
    vad_threshold: float,
    merge_gap_ms: int = 250,
    min_speech_ms: int = 300,
):
    if vad_backend == "energy":
        return EnergyVadAdapter(threshold=vad_threshold, merge_gap_ms=merge_gap_ms, min_speech_ms=min_speech_ms)
    if vad_backend == "mock":
        return MockVADAdapter()
    if vad_backend == "command":
        if not vad_command:
            raise typer.BadParameter("--vad-command is required when --vad-backend command")
        return CommandVADAdapter(command=vad_command.split())
    if vad_backend == "funasr":
        command = vad_command.split() if vad_command else ["python3", "scripts/funasr_vad_wrapper.py"]
        return CommandVADAdapter(command=command)
    raise typer.BadParameter("--vad-backend must be 'energy', 'mock', 'command', or 'funasr'")


def _build_asr(
    *,
    asr_backend: str,
    asr_command: str | None,
    mock_text: str | None,
    language: str = "zh",
    model_name: str = "mock-asr",
):
    if asr_backend == "mock":
        return MockASRAdapter(text=mock_text, language=language, model_name=model_name)
    if asr_backend == "command":
        if not asr_command:
            raise typer.BadParameter("--asr-command is required when --asr-backend command")
        return CommandASRAdapter(command=asr_command.split())
    if asr_backend == "funasr":
        command = asr_command.split() if asr_command else ["python3", "scripts/funasr_sensevoice_wrapper.py"]
        return CommandASRAdapter(command=command)
    raise typer.BadParameter("--asr-backend must be 'mock', 'command', or 'funasr'")


def _build_llm(*, llm_backend: str, llm_command: str | None):
    if llm_backend == "rule_based":
        return RuleBasedLLMAdapter()
    if llm_backend == "mock":
        return MockLLMAdapter()
    if llm_backend == "command":
        if not llm_command:
            raise typer.BadParameter("--llm-command is required when --llm-backend command")
        return CommandLLMAdapter(command=llm_command.split())
    raise typer.BadParameter("--llm-backend must be 'rule_based', 'mock', or 'command'")


@app.command(name="daily-status")
def daily_status(
    day: str = typer.Option(..., help="Daily report day in YYYY-MM-DD format."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(
        None,
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = _load_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    typer.echo(f"day={day} status={get_daily_report_status(config=config, day=day)}")
