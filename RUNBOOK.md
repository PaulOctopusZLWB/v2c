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
5. `CommandASRAdapter` for local commands or Docker wrapper scripts that emit normalized ASR JSON.

It also implements the LLM text-processing boundary before provider integration:

1. `LLMPort` as the core-owned text-only context generation interface.
2. A deterministic `RuleBasedLLMAdapter` for local smoke tests.
3. `daily_summaries` storage for summary, todos, facts, and inferences.
4. `pcn summarize` to generate daily context and memory candidates from transcript text only.

It also implements the human review boundary:

1. `pcn publish-review` writes pending memory candidates into `30_Memory_Candidates/YYYY-MM-DD.md`.
2. The user confirms a candidate by changing `- [ ]` to `- [x]`.
3. `pcn confirm-review` parses checked candidates, creates confirmed memory cards, and emits signed events.
4. `pcn memory-verify` rechecks stored signed events and flags tampered payloads.

It also implements the speaker review boundary:

1. `pcn publish-speaker-review` writes `90_System/Speaker_Review/YYYY-MM-DD.md`.
2. The user edits speaker mapping lines such as `- self: Paul`.
3. Segment-level lines can override a specific segment when changed to a concrete person label.
4. `pcn sync-speaker-review` reads mappings and segment overrides back into SQLite without overwriting raw transcript speaker labels.

It also implements the archive boundary:

1. `ArchivePort` as the core-owned archive interface.
2. A local filesystem archive adapter that can target a mounted NAS path.
3. Hash verification before `audio_files.status` becomes `archived`.
4. `pcn archive` for raw audio archive smoke tests.

It also implements launchd template generation:

1. `pcn launchd-write-plists` writes ingest, process, daily, and archive plist templates.
2. Templates use `uv run pcn ...` commands and per-job log paths.
3. The command writes project files only; it does not call `launchctl` or install into `~/Library/LaunchAgents`.

It also implements a minimal diagnostics boundary:

1. `job_runs` records run id, job name, status, timestamps, and error text.
2. `pcn memory-verify` records a job run.
3. `pcn job-status` lists recent job runs for launchd/manual diagnostics.

Real FunASR/Silero VAD, FunASR/SenseVoice transcription, cloud/local LLM provider adapters, rsync/restic-specific NAS behavior, and launchctl install/uninstall are not implemented yet. The energy VAD, mock ASR, and rule-based LLM are not the final production intelligence adapters; they exist to make the chunking, storage, transcript, context-generation, review, archive, and scheduling boundaries testable before model integration.

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
uv run pcn summarize \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --day 2087-05-10
uv run pcn publish-review \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --day 2087-05-10
uv run pcn publish-speaker-review \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --day 2087-05-10
uv run pcn archive \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --archive-root .smoke-nas
uv run pcn launchd-write-plists \
  --output-dir .smoke-launchd \
  --working-directory "$PWD" \
  --data-dir data \
  --obsidian-vault /Users/paul/Documents/Obsidian/PersonalContext \
  --source-dir sample_data \
  --archive-root .smoke-nas
```

## Configuration

Copy `config/local.example.toml` to `config/local.toml` and adjust local paths/backends. The archive command can read this file directly:

```bash
uv run pcn archive --config config/local.toml
```

Explicit CLI options override config-file paths where supported.

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

To use a command/Docker ASR wrapper instead of mock ASR, the command must accept one chunk WAV path and print:

```json
{
  "model_name": "sensevoice",
  "model_version": "local-version",
  "segments": [
    {
      "text": "转写文本",
      "start_ms": 0,
      "end_ms": 1200,
      "confidence": 0.88,
      "language": "zh"
    }
  ]
}
```

Smoke the contract with the example wrapper:

```bash
uv run pcn transcribe \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --asr-backend command \
  --asr-command "python3 scripts/asr_wrapper_example.py"
```

Expected rule-based summary smoke output after mock ASR:

```text
summaries_created=1 memory_candidates_created=<n>
```

After editing `.smoke-vault/30_Memory_Candidates/2087-05-10.md` and changing one candidate from `- [ ]` to `- [x]`, confirm it:

```bash
uv run pcn confirm-review \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --day 2087-05-10
uv run pcn memory-verify \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault
uv run pcn job-status \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault
```

Expected confirmation output:

```text
candidates_confirmed=1 signed_events_created=1
```

Expected memory verification output after confirmation:

```text
total_events=1 valid_events=1 invalid_events=0
```

Expected job status output shape:

```text
run_id=run_... job_name=memory-verify status=succeeded error=
```

After editing `.smoke-vault/90_System/Speaker_Review/2087-05-10.md`, sync speaker mappings:

```bash
uv run pcn sync-speaker-review \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --day 2087-05-10
```

Expected speaker sync output shape:

```text
mappings_upserted=<n> segment_overrides_upserted=<n>
```

Expected archive smoke output with the sample data:

```text
files_archived=7 files_pending=0
```

Expected launchd template output:

```text
plists_written=4 output_dir=.smoke-launchd
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
