# Personal Context Node Runbook

## Current Implemented Scope

This repository currently implements the first milestone from `IMPLEMENTATION_PLAN.md`:

1. Import WAV files from a source directory into local raw storage.
2. Register imported audio in SQLite.
3. Produce deterministic mock transcript segments.
4. Generate memory candidates with transcript evidence references.
5. Optionally confirm the first candidate into a signed `memory_card.confirmed.v1` event.
6. Publish a daily Markdown note to the configured PersonalContext Obsidian vault.

It also implements the first audio preprocessing boundary:

1. `VADPort` as the core-owned voice activity detection interface.
2. A deterministic local `EnergyVadAdapter` used as a fallback and test adapter.
3. SQLite persistence for `speech_ranges` and `audio_chunks`.
4. Work WAV chunk generation under `data/audio/work/YYYY-MM-DD/`.
5. A `pcn preprocess` CLI command.

It also implements the ASR boundary before real model integration:

1. `ASRPort` as the core-owned transcription interface.
2. A deterministic `MockASRAdapter` for tests and local wiring.
3. `pcn transcribe` to process `pending_asr` chunks into transcript segments.
4. Transcript storage now records `chunk_id`, confidence, ASR backend, model name, and model version.

Real FunASR/Silero VAD, FunASR/SenseVoice transcription, LLM summaries, speaker review read-back, NAS archive, and launchd jobs are not implemented yet. The energy VAD and mock ASR are not the final production audio intelligence adapters; they exist to make the chunking, storage, and transcript boundaries testable before model integration.

## Local uv Run

```bash
uv sync
uv run pytest -q
uv run pcn run-first-milestone \
  --source-dir sample_data \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --confirm-first-candidate
uv run pcn preprocess \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --vad-threshold 0.01 \
  --max-chunk-ms 30000
uv run pcn transcribe \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --mock-text "真实样本的本地 mock ASR 输出"
```

Expected first milestone smoke output:

```text
imported_files=7 transcript_segments=7 memory_candidates=7 signed_events=1
```

Expected preprocessing smoke output shape:

```text
audio_files_processed=7 speech_ranges_created=<n> audio_chunks_created=<n>
```

With the current energy VAD and the checked-in real samples, `vad-threshold 0.01` produced one range/chunk in local verification. Treat this as adapter smoke coverage, not ASR-quality evidence.

Expected mock ASR smoke output after preprocessing:

```text
chunks_transcribed=1 segments_created=1
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
