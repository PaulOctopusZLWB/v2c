from __future__ import annotations

from pathlib import Path

import typer

from personal_context_node.adapters.archive.local_filesystem import LocalFilesystemArchiveAdapter
from personal_context_node.adapters.asr.command import CommandASRAdapter
from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.archive import archive_completed_audio
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.jobs import job_status_rows, record_job_run
from personal_context_node.launchd import write_launchd_plists
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.pipeline import run_first_milestone as run_first_milestone_pipeline
from personal_context_node.speaker_review import publish_speaker_review, sync_speaker_review
from personal_context_node.tasks import process_status_rows
from personal_context_node.transcription import transcribe_pending_chunks


app = typer.Typer(help="Personal Context Node local pipeline.")


@app.callback()
def main() -> None:
    """Run local-first Personal Context Node jobs."""


@app.command()
def run_first_milestone(
    source_dir: Path = typer.Option(..., exists=True, file_okay=False, help="Directory containing WAV recordings."),
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    confirm_first_candidate: bool = typer.Option(False, help="Confirm the first generated candidate for smoke tests."),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
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


@app.command()
def preprocess(
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    vad_threshold: float = typer.Option(0.03, min=0.0, max=1.0, help="Energy VAD RMS threshold."),
    max_chunk_ms: int = typer.Option(30_000, min=100, help="Maximum ASR chunk duration in milliseconds."),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(threshold=vad_threshold),
        max_chunk_ms=max_chunk_ms,
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
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    mock_text: str = typer.Option("模拟本地转写", help="Text emitted by the mock ASR adapter."),
    asr_backend: str = typer.Option("mock", help="ASR backend: mock or command."),
    asr_command: str | None = typer.Option(None, help="Command ASR wrapper, e.g. 'python scripts/funasr_wrapper.py'."),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    if asr_backend == "mock":
        asr = MockASRAdapter(text=mock_text)
    elif asr_backend == "command":
        if not asr_command:
            raise typer.BadParameter("--asr-command is required when --asr-backend command")
        asr = CommandASRAdapter(command=asr_command.split())
    else:
        raise typer.BadParameter("--asr-backend must be 'mock' or 'command'")
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
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = generate_daily_context(config=config, day=day, llm=RuleBasedLLMAdapter())
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
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    review_path = publish_candidate_review(config=config, day=day)
    typer.echo(f"review_path={review_path}")


@app.command()
def confirm_review(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = confirm_checked_candidates(config=config, day=day)
    typer.echo(
        " ".join(
            [
                f"candidates_confirmed={result.candidates_confirmed}",
                f"signed_events_created={result.signed_events_created}",
            ]
        )
    )


@app.command(name="publish-speaker-review")
def publish_speaker_review_cmd(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    review_path = publish_speaker_review(config=config, day=day)
    typer.echo(f"review_path={review_path}")


@app.command(name="sync-speaker-review")
def sync_speaker_review_cmd(
    day: str = typer.Option(..., help="Review day in YYYY-MM-DD format."),
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = sync_speaker_review(config=config, day=day)
    typer.echo(
        " ".join(
            [
                f"mappings_upserted={result.mappings_upserted}",
                f"segment_overrides_upserted={result.segment_overrides_upserted}",
            ]
        )
    )


@app.command()
def archive(
    archive_root: Path | None = typer.Option(None, help="NAS or local archive root."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    require_existing_root: bool = typer.Option(False, help="Treat a missing archive root as unavailable."),
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
            ]
        )
    )


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
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    source_dir: Path = typer.Option(Path("/Volumes/DJI"), help="Mounted DJI source directory."),
    archive_root: Path = typer.Option(Path("/Volumes/NAS/PersonalContext"), help="NAS archive root."),
) -> None:
    paths = write_launchd_plists(
        output_dir=output_dir,
        working_directory=str(working_directory),
        data_dir=str(data_dir),
        obsidian_vault=str(obsidian_vault),
        source_dir=str(source_dir),
        archive_root=str(archive_root),
        dry_run=True,
    )
    typer.echo(f"plists_written={len(paths)} output_dir={output_dir}")


@app.command(name="memory-verify")
def memory_verify(
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
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
            ]
        )
    )


@app.command(name="job-status")
def job_status(
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
    limit: int = typer.Option(20, min=1, help="Maximum rows to print."),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    rows = job_status_rows(config=config, limit=limit)
    for row in rows:
        typer.echo(
            " ".join(
                [
                    f"run_id={row['run_id']}",
                    f"job_name={row['job_name']}",
                    f"status={row['status']}",
                    f"error={row['error'] or ''}",
                ]
            )
        )


@app.command(name="process-status")
def process_status(
    data_dir: Path = typer.Option(Path("data"), help="Local data directory."),
    obsidian_vault: Path = typer.Option(
        Path("/Users/paul/Documents/Obsidian/PersonalContext"),
        help="Dedicated PersonalContext Obsidian vault path.",
    ),
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
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
                ]
            )
        )
