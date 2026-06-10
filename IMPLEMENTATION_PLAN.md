# Personal Context Node Implementation Plan

## Phase 0: Repository Foundation

Goal: create a minimal project skeleton that preserves the architecture boundaries before any model-specific code is added.

Tasks:

1. Create package layout for `core`, `adapters`, `protocols`, and `scripts`.
2. Add configuration loading for local paths, model backend choice, Obsidian vault path, and NAS archive path.
3. Add SQLite schema migrations for files, tasks, segments, summaries, memory candidates, memory cards, and signed events.
4. Add logging with run IDs so launchd jobs can be diagnosed.
5. Add test fixtures using tiny audio samples or mocked ASR outputs.

Acceptance:

1. A developer can run a local CLI health check.
2. SQLite can initialize from scratch.
3. No core domain module imports a concrete ASR, LLM, Obsidian, NAS, or launchd adapter.

## Phase 1: Import Pipeline

Goal: reliably copy DJI Mic 3 recordings into local storage and register them once.

Tasks:

1. Implement mounted-volume discovery.
2. Implement file discovery for WAV recordings.
3. Copy files into `data/audio/raw/YYYY-MM-DD/`.
4. Wait for stable file size before importing.
5. Compute content hash.
6. Register imported files in SQLite with status.
7. Add duplicate detection.

Acceptance:

1. Re-running import does not duplicate files.
2. Unplugging during copy does not produce a registered corrupt file.
3. File state is inspectable from CLI.

## Phase 2: VAD and Chunking

Goal: reduce long recordings to effective speech ranges before ASR.

Tasks:

1. Define `VADPort`.
2. Implement primary VAD adapter using FunASR VAD or Silero VAD.
3. Store speech ranges with start/end timestamps.
4. Merge nearby speech ranges.
5. Create ASR chunks sized for reliable transcription.

Acceptance:

1. 8 hours of mostly quiet audio can be reduced into speech chunks.
2. Speech ranges remain traceable to the source WAV.
3. Chunking is deterministic for the same inputs and config.

## Phase 3: ASR Backend

Goal: produce local transcripts from speech chunks.

Tasks:

1. Define `ASRPort`.
2. Implement FunASR + SenseVoice adapter.
3. Store segment text, timestamps, confidence if available, and source chunk refs.
4. Default speaker to `self` for owner-worn mic recordings.
5. Add backend selection config.
6. Add placeholder adapter boundary for faster-whisper fallback.

Acceptance:

1. Chinese recordings transcribe locally.
2. ASR failures are retriable by task ID.
3. Transcripts can be regenerated without losing raw evidence.

## Phase 4: Speaker Review

Goal: support lightweight correction without building a Web UI.

Tasks:

1. Store `speaker_cluster`, `person`, and segment-level override fields.
2. Generate Markdown review files with speaker mapping blocks.
3. Parse edited mapping blocks back into SQLite.
4. Support merge by mapping multiple clusters to the same person.
5. Support split by segment-level person override.

Acceptance:

1. The owner can correct speaker labels in Markdown.
2. Corrections update materialized transcript views.
3. Raw ASR and raw cluster labels remain preserved.

## Phase 5: Daily Markdown Publishing

Goal: produce useful human-readable daily context in the dedicated Obsidian vault.

Tasks:

1. Create `/Users/paul/Documents/Obsidian/PersonalContext` if missing.
2. Create standard folders.
3. Generate daily notes with metrics, people, topics, decisions, todos, facts, inferences, memory candidates, and source sessions.
4. Generate session notes when useful.
5. Keep generated sections clearly marked.

Acceptance:

1. Running the daily job creates or updates the correct daily note.
2. Notes do not write into the Supcon vault.
3. Source session links remain stable.

## Phase 6: LLM Summaries and Memory Candidates

Goal: use LLM text processing without exposing raw audio or coupling to one provider.

Tasks:

1. Define `LLMPort`.
2. Implement chunk summary, session summary, and daily summary prompts.
3. Extract memory candidates as atomic claims.
4. Require evidence refs for generated candidates.
5. Separate facts from inferences.
6. Store candidate claim and eventual edited claim separately.

Acceptance:

1. Daily reports can be produced from transcript text.
2. Generated memory candidates are atomic.
3. Every generated candidate has at least one evidence reference.

## Phase 7: Memory Card Event Log

Goal: turn confirmed memory candidates into signed, shareable protocol events.

Tasks:

1. Implement `memory_card.v1` validation.
2. Implement `memory_annotation.v1` validation.
3. Implement canonical JSON serialization.
4. Implement Ed25519 signing and verification.
5. Generate key identity profile locally.
6. Emit signed events for card creation, confirmation, metadata updates, supersession, and revocation.
7. Materialize current card state from event log.

Acceptance:

1. Confirmed cards produce signed events.
2. Invalid signatures are rejected.
3. Semantic claim changes require a new card and supersede event.
4. Event log can rebuild current memory state from scratch.

## Phase 8: NAS Archive

Goal: move durable evidence and outputs to cold storage after processing.

Tasks:

1. Define `ArchivePort`.
2. Implement NAS sync adapter.
3. Sync raw audio, transcripts, summaries, and event logs.
4. Verify copied hashes.
5. Mark files as archived.
6. Add local cleanup eligibility rules for hot raw audio.

Acceptance:

1. NAS unavailability does not block local transcript completion.
2. Archived files are hash-verified.
3. Local cleanup never deletes unarchived raw audio.

## Phase 9: launchd Integration

Goal: run the system automatically without a long-lived service.

Tasks:

1. Add launchd plist templates for ingest, process, daily, and archive jobs.
2. Add CLI commands that each job can call idempotently.
3. Add log paths and failure exit codes.
4. Add install and uninstall scripts for launchd jobs.

Acceptance:

1. Jobs exit cleanly when no new files exist.
2. Import starts after DJI Mic 3 is connected and detected.
3. Daily job generates output only when new data exists.

## Phase 10: Verification Harness

Goal: make the pipeline testable before using real daily archives.

Tasks:

1. Add unit tests for protocol validation, canonicalization, signing, and event materialization.
2. Add integration tests with mocked ASR and LLM adapters.
3. Add a small end-to-end fixture from imported file to daily Markdown.
4. Add dry-run mode for launchd commands.
5. Add schema migration tests.

Acceptance:

1. Tests can run without model downloads.
2. Protocol tests do not depend on audio tooling.
3. A dry run shows pending work without mutating state.

## Development Order

Recommended first implementation slice:

1. Repository foundation.
2. SQLite schema.
3. Protocol schema and signing tests.
4. Import pipeline.
5. Mock ASR end-to-end path.
6. Real FunASR adapter.
7. Obsidian publishing.
8. LLM memory candidates.

This order validates the architecture before depending on model performance.

## Risks

| Risk | Mitigation |
| --- | --- |
| FunASR setup friction on macOS Apple Silicon | Keep ASR behind `ASRPort`; maintain faster-whisper fallback |
| Long audio processing takes too long | Run VAD first; process chunks incrementally |
| ASR hallucination or misrecognition | Keep evidence refs and raw transcript; require human confirmation |
| Speaker confusion | Default self from mic prior; expose Markdown correction |
| Memory pollution | Candidate review before confirmation |
| Protocol overengineering | Keep transport, encryption, and Web UI out of v1 |
| Obsidian clutter | Use a dedicated vault and generated-folder conventions |

## First Milestone

The first milestone is complete when a mocked audio import can produce:

1. A registered file row.
2. Mocked speech segments.
3. A daily Markdown note in the PersonalContext vault.
4. A memory candidate with evidence refs.
5. A confirmed memory card signed as an event.

The second milestone replaces mocked ASR with FunASR + SenseVoice.
