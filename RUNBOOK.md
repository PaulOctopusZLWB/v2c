# Personal Context Node Runbook

## Current Implemented Scope

This repository currently implements the first milestone from `IMPLEMENTATION_PLAN.md`:

1. Import WAV files from a source directory into local raw storage.
2. Register imported audio in SQLite.
3. Produce deterministic mock transcript segments.
4. Generate memory candidates with transcript evidence references.
5. Optionally confirm the first candidate into a signed `memory_card.confirmed.v1` event.
6. Publish a daily Markdown note to the configured PersonalContext Obsidian vault.

Real VAD, FunASR/SenseVoice transcription, LLM summaries, speaker review read-back, NAS archive, and launchd jobs are not implemented yet.

## Local uv Run

```bash
uv sync
uv run pytest -q
uv run pcn run-first-milestone \
  --source-dir sample_data \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --confirm-first-candidate
```

Expected smoke output:

```text
imported_files=7 transcript_segments=7 memory_candidates=7 signed_events=1
```

## Docker Run

Docker pulls the `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` base image itself.

```bash
docker compose build
docker compose run --rm personal-context-node
```

The compose run mounts:

- `./sample_data` as read-only input.
- `./data` as local SQLite/raw-audio output.
- `/Users/paul/Documents/Obsidian/PersonalContext` as the Obsidian vault.
