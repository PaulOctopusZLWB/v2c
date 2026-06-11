from __future__ import annotations

from pathlib import Path

import typer

from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone as run_first_milestone_pipeline


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
