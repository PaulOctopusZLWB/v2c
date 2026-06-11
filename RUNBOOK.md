# Personal Context Node Runbook

## Current Implemented Scope

This repository currently implements the first milestone from `IMPLEMENTATION_PLAN.md`:

1. `pcn init` creates local data directories, SQLite schema, Obsidian vault folders, and an optional TOML config.
2. `pcn health` checks SQLite initialization and Obsidian vault writability.
3. Import WAV files from a source directory into local raw storage.
4. Register imported audio in SQLite.
5. Produce deterministic mock transcript segments.
6. Generate memory candidates with transcript evidence references.
7. Optionally confirm the first candidate into a signed `memory_card.created` event.
8. Publish a daily Markdown note to the configured PersonalContext Obsidian vault.

It also implements the first audio preprocessing boundary:

1. `VADPort` as the core-owned voice activity detection interface.
2. A deterministic local `EnergyVadAdapter` used as a fallback and test adapter.
3. SQLite persistence for `speech_ranges` and `audio_chunks`.
4. Work WAV chunk generation under `data/audio/work/YYYY-MM-DD/`.
5. `CommandVADAdapter` for local commands or Docker wrapper scripts that emit normalized VAD JSON.
6. A `pcn preprocess` CLI command.

It also implements the ASR boundary before real model integration:

1. `ASRPort` as the core-owned transcription interface.
2. A deterministic `MockASRAdapter` for tests and local wiring.
3. `pcn transcribe` to process `pending_asr` chunks into transcript segments.
4. Transcript storage now records `chunk_id`, confidence, ASR backend, model name, and model version.
5. `CommandASRAdapter` for local commands or Docker wrapper scripts that emit normalized ASR JSON.

It also implements the LLM text-processing boundary before provider integration:

1. `LLMPort` as the core-owned text-only context generation interface.
2. A deterministic `RuleBasedLLMAdapter` for local smoke tests.
3. Legacy `daily_summaries` storage plus formal `summaries` rows using `daily_summary.v1`.
4. `pcn summarize` to generate daily context and memory candidates from transcript text only.
5. `CommandLLMAdapter` for local or cloud wrapper commands that receive transcript JSON on stdin and emit normalized daily context JSON.

It also implements the human review boundary:

1. `pcn publish-review` writes pending memory candidates into `30_Memory_Candidates/YYYY-MM-DD.md`.
2. The user confirms a candidate by changing `- [ ]` to `- [x]`.
3. `pcn confirm-review` parses checked candidates, creates confirmed memory cards, and emits signed `memory_card.created` events.
4. `pcn memory-verify` rechecks stored signed events, canonical signing body hashes, and owner hash-chain links.
5. `pcn memory-verify` also rebuilds the trusted materialized memory card view and diffs it against `memory_cards`.
6. `pcn memory-export --since ...` writes trusted `raw_event_json` rows as JSONL for exchange/backup.
7. `signed_events` stores `event_hash`, `owner_sequence`, `prev_event_hash`, `raw_event_json`, `signing_body_json`, and `trust_status`.

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

It also implements launchd template generation and controlled install/uninstall:

1. `pcn launchd-write-plists` writes ingest, process, daily, and archive plist templates.
2. Templates use `uv run pcn ...` commands and per-job log paths.
3. `pcn launchd-install` copies generated plist files into `~/Library/LaunchAgents` and runs `launchctl bootstrap` only when `--execute` is passed.
4. `pcn launchd-uninstall` runs `launchctl bootout` and removes plist files only when `--execute` is passed.
5. Both install commands default to dry-run and print the launchctl commands they would run.

It also implements a minimal diagnostics boundary:

1. `job_runs` records run id, job name, status, timestamps, and error text.
2. `pcn memory-verify` records a job run.
3. `pcn job-status` lists recent job runs for launchd/manual diagnostics.

It also implements the task lifecycle foundation:

1. `tasks` stores `pending -> claimed -> running -> succeeded/failed_retryable/failed_terminal`.
2. `task_type + target_type + target_id` is unique.
3. `claimed_by_run_id` and `claimed_at` support lease-based recovery.
4. Import registers the first `vad` task for each new audio file in the same SQLite transaction.
5. `pcn process-status` lists current task state.
6. `pcn process-run` claims one pending task, runs VAD, ASR, or session derivation, and records success/failure.
7. `pcn process-retry --task-id ...` resets a failed task to `pending`.
8. `pcn process-rerun --task-type ... --target-type ... --target-id ...` reopens or enqueues a deterministic task target.

It also implements session derivation:

1. `sessions` groups transcript segments between ASR and daily reports.
2. Sessions are split by deterministic time gap, defaulting to 20 minutes.
3. Re-derivation reuses an existing `session_id` when the first segment is unchanged.
4. ASR task fan-in registers one `session_derive` task for the affected `date_key` when all chunks for an audio file are transcribed.
5. `summaries` stores `session_summary.v1` JSON for each session.
6. `pcn publish-session-notes` writes `20_Conversations/YYYY-MM-DD/ses_*.md` notes without embedding full transcripts.

It also implements active transcript semantics:

1. `transcript_segments` has `is_active` and `asr_run_id`.
2. A successful ASR task deactivates previous active transcript segments for the same audio file before inserting new segments.
3. Session derivation and daily context generation read only active transcript segments.
4. This lets ASR reruns replace materialized context without deleting historical evidence rows.

It also implements the daily/publish task DAG:

1. `session_derive` success enqueues `summarize_session` for each derived session.
2. `summarize_session` stores `session_summary.v1` in `summaries`.
3. When all session summaries for the day have succeeded, the runner enqueues `daily_generate`.
4. `daily_generate` creates daily context and enqueues `obsidian_publish`.
5. `obsidian_publish` writes session notes, memory candidate review, and speaker review.
6. Repeated `pcn process-run` calls can now advance VAD -> ASR -> session -> session summary -> daily -> Obsidian publish.

Real FunASR/Silero VAD, FunASR/SenseVoice transcription, cloud/local LLM provider adapters, and rsync/restic-specific NAS behavior are not implemented yet. The energy VAD, mock ASR, and rule-based LLM are not the final production intelligence adapters; they exist to make the chunking, storage, transcript, session, context-generation, review, archive, scheduling, and protocol boundaries testable before model integration.

## Local uv Run

```bash
uv sync
uv run pytest -q
uv run pcn init \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --config-path .smoke-config/local.toml
uv run pcn health \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault
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
uv run pcn publish-session-notes \
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

To use a command/Docker VAD wrapper instead of energy VAD, the command must accept one WAV path and print:

```json
{
  "ranges": [
    {"start_ms": 120, "end_ms": 4200}
  ]
}
```

Example:

```bash
uv run pcn preprocess \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --vad-backend command \
  --vad-command "python3 scripts/funasr_vad_wrapper.py" \
  --max-chunk-ms 30000
```

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

For a real FunASR/SenseVoice runtime, install FunASR in the uv or Docker environment that executes the wrapper, then use:

```bash
uv run pcn transcribe \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --asr-backend command \
  --asr-command "python3 scripts/funasr_sensevoice_wrapper.py --model iic/SenseVoiceSmall --model-version local"
```

The wrapper lazy-loads `funasr.AutoModel` and emits the same normalized JSON contract consumed by `CommandASRAdapter`. Core pipeline code does not import FunASR directly.

Expected rule-based summary smoke output after mock ASR:

```text
summaries_created=1 memory_candidates_created=<n>
```

To use a command LLM wrapper instead of rule-based text processing:

```bash
uv run pcn summarize \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --day 2087-05-10 \
  --llm-backend command \
  --llm-command "python3 scripts/llm_wrapper_example.py"
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
uv run pcn process-status \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault
uv run pcn process-run \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --vad-threshold 0.01 \
  --max-chunk-ms 30000
```

Expected confirmation output:

```text
candidates_confirmed=1 signed_events_created=1
```

Expected memory verification output after confirmation:

```text
total_events=1 valid_events=1 invalid_events=0 materialization_mismatches=0
```

Expected memory export output:

```bash
uv run pcn memory-export \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --since 2000-01-01 \
  --output-path .smoke-memory/events.jsonl
```

```text
events_exported=1 output_path=.smoke-memory/events.jsonl
```

Expected job status output shape:

```text
run_id=run_... job_name=memory-verify status=succeeded error=
```

After importing the seven sample files, `pcn process-status` should show seven pending `vad` tasks.
Repeated `pcn process-run` calls advance those tasks, enqueue/run `asr` tasks for generated chunks, run `session_derive`, run `summarize_session`, generate daily context, and publish Obsidian review/session notes.

Manual task recovery:

```bash
uv run pcn process-retry \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --task-id task_...
uv run pcn process-rerun \
  --data-dir .smoke-data \
  --obsidian-vault .smoke-vault \
  --task-type asr \
  --target-type audio_chunk \
  --target-id chk_...
```

It also implements the daily report lifecycle:

1. `daily_reports` stores status per day.
2. `pcn summarize` marks the day `generated`.
3. `pcn publish-review` marks the day `review_pending`.
4. `pcn confirm-review` marks the day `review_synced` after at least one candidate is confirmed.
5. `pcn daily-status --day YYYY-MM-DD` prints the current status.

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

Dry-run install preview:

```bash
uv run pcn launchd-install \
  --plist-dir .smoke-launchd \
  --launch-agents-dir ~/Library/LaunchAgents
```

Actual install requires the explicit `--execute` flag:

```bash
uv run pcn launchd-install \
  --plist-dir .smoke-launchd \
  --launch-agents-dir ~/Library/LaunchAgents \
  --execute
```

Uninstall also defaults to dry-run:

```bash
uv run pcn launchd-uninstall --launch-agents-dir ~/Library/LaunchAgents
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
