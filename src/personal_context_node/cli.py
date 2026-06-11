from __future__ import annotations

from pathlib import Path

import typer

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.pipeline import run_first_milestone as run_first_milestone_pipeline
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
) -> None:
    config = AppConfig(data_dir=data_dir, obsidian_vault=obsidian_vault)
    result = transcribe_pending_chunks(config=config, asr=MockASRAdapter(text=mock_text))
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
