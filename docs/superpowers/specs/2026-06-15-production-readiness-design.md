# Personal Context Node Production Readiness Design

Date: 2026-06-15

## Objective

Bring the current local-first Personal Context Node project to a production-usable state for a single macOS owner.

The production target is practical local operation:

- DJI Mic audio import and local processing can run repeatedly without corrupting evidence.
- SQLite task state remains recoverable across crashes, retries, and long-running model calls.
- Obsidian publishing is safe, explicit, and reviewable by the owner.
- The local Web control panel is usable for setup, import, processing, review, and diagnostics.
- Missing LLM credentials or missing LLM command configuration must not block the core audio-to-transcript workflow.
- Mock fixture output must not be written into real notes, summaries, or memory candidates unless the user explicitly requested mock mode.

## Boundary Conditions

This design covers local macOS single-user production readiness. It does not include multi-user memory exchange, cloud deployment, MCP wrapping, mobile apps, or a persistent FunASR daemon as required scope.

The current system already has broad CLI, SQLite, Obsidian, launchd, archive, and Web foundations. The goal is to harden and connect those foundations, not replace the architecture.

The implementation must preserve the existing ports-and-adapters direction:

```text
core/domain/service code -> ports -> adapters
```

Adapters may depend on local tools, filesystems, and command wrappers. Core task and evidence logic must not depend on FunASR, launchd, Obsidian internals, or a specific LLM provider SDK.

## Current Evidence

Baseline verification before this design:

- `uv run pytest -q`: 493 passed, 1 warning.
- `cd web && npm test`: 20 passed, with a jsdom `HTMLMediaElement.play` stderr warning.
- `cd web && npm run build`: passed.
- Live Playwright e2e failed waiting for a `2087-...` day button after import/run. The page snapshot showed tasks running, but the date navigation had not refreshed.
- First round of three independent subagent reviews found actionable issues in backend/data safety, frontend/usability, and operations/performance.

## Approach Options

### Option A: Layered Production Hardening

Fix data safety and recoverability first, then operations, then Web usability, then low-risk efficiency improvements. Run verification and three independent reviews after each substantial batch.

This is the recommended path because the most dangerous failures are data pollution, incorrect deletion, stuck workers, and invisible failures. Web improvements then make those states operable.

### Option B: UI-First Polish

Make the Web control panel feel better first, then revisit backend and operations. This creates visible progress but leaves mock LLM pollution, path ambiguity, launchd failure, and task recovery risks in place.

This is not recommended for production readiness.

### Option C: Performance-First Rewrite

Focus first on FunASR model reuse, streaming conversion, and batching. This may reduce runtime significantly but expands the change surface before correctness and operability are stable.

This is deferred. A later performance track can add a FunASR daemon or batch adapter once the production safety baseline is reliable.

## Recommended Design

Use Option A.

Implementation is divided into four layers. Each layer should be implemented with targeted tests first, followed by production code, then verification. After a layer or coherent set of layers is green, run three independent subagent reviews again and fix any concrete issues.

## Layer 1: Production Safety Baseline

### LLM Degradation

Default no-key behavior must not use fixture-backed mock output.

Changes:

- Change `AppConfig.llm_backend` default from `mock` to `rule_based`.
- Keep `MockLLMAdapter` available for tests and explicit `--mock` CLI mode.
- Ensure generated/example production configs use `rule_based` unless they are clearly labeled test/mock configs.
- If `llm_backend = "command"` has no command, LLM-dependent tasks fail terminally with an actionable error. Audio import, VAD, ASR, and session derivation must still work without a usable command LLM.

Expected behavior:

- A fresh local run without an LLM key or command can import audio, run VAD, run ASR, derive sessions, and publish reviewable transcript artifacts.
- Daily/session summaries use deterministic local `rule_based` processing by default.
- No fixture text from `MockLLMAdapter` enters production notes or memory candidates unless `--mock` or a mock config is explicitly selected.

### Task Retry and Lease Recovery

Manual retry must be immediate and visible.

Changes:

- `retry_task()` resets `retry_count`, `attempt_count`, `available_at`, claim fields, start/finish timestamps, and `last_error`.
- Web retry should start or resume the worker after requeueing, or clearly label the action as only requeueing. The preferred Web behavior is retry and run.

Long-running tasks must not silently block forever.

Changes:

- Add command timeouts for VAD, ASR, LLM, and archive command adapters.
- Convert timeout failures into retryable task failures with useful errors.
- Keep the current run-id guarded final `succeed_task()` behavior.
- Do not attempt a broad transactional rewrite of all VAD/ASR/LLM side effects in this batch. Instead, reduce the stale-worker window with timeouts and make retry/recovery explicit. A later task-owner heartbeat can be added if repeated real workloads prove lease expiry is common.

### Archive Cleanup Safety

Local raw audio deletion must be fail-closed.

Changes:

- Before deleting a local raw file, verify that the resolved local path is inside `config.raw_audio_dir`.
- Verify the local file hash still matches the archive record hash.
- If either check fails, do not delete the file. Keep the audio file in `cleanup_eligible` and update the archive record with a failed/pending cleanup status and `last_error`.

### Path Resolution

Config-driven runs must resolve paths consistently.

Changes:

- Resolve `obsidian_vault` and `nas_archive_root` through the same expanduser/absolute path logic as `data_dir`.
- Change config-aware CLI path options in `ingest-import`, `process-run`, `archive`, and `launchd-write-plists` to default to `None`, so omitted options do not replace config values with hard-coded local defaults.
- Keep explicit CLI overrides authoritative.

## Layer 2: Operations and Startup Reliability

### Web Startup

The one-command local Web path must either serve the control panel or fail clearly.

Changes:

- `scripts/start-web.sh` checks for `web/dist/index.html`.
- If missing, run `npm --prefix web install` only when `web/node_modules` is absent, then run `npm --prefix web run build` before starting the backend.
- If the build command fails, print the failing command and exit non-zero instead of serving API-only while telling the user to open `/app/`.

### Launchd

Launchd plists must be self-contained enough to run after login.

Changes:

- Resolve `uv` to an absolute executable path when writing plists.
- Add an explicit launchd `PATH` environment.
- Use absolute log paths and create the launchd log directory when writing or installing plists.
- For scheduled ingest, only include `--source-dir` when it is explicitly configured. Otherwise let `ingest-import --config ...` use device discovery.

### Docker Context

Docker builds must not send the whole local data workspace.

Changes:

- Add `.dockerignore` for local runtime data and caches: `.venv/`, `.tmp/`, `data/`, `sample_data/`, `web/node_modules/`, `web/dist/`, pytest/ruff caches, macOS metadata, and local logs.
- Keep source, lock files, config examples, and scripts in the build context.

## Layer 3: Web Usability and Diagnostics

### Bootstrap Errors

The control panel must distinguish "empty project" from "API failed".

Changes:

- Track loading/error states for health, devices, days, persons, and task status bootstrap.
- Show an actionable top-level error with retry when bootstrap calls fail.
- Preserve last-known data only for transient refresh failures after a successful load.

### Navigation Refresh

The date/session navigation must update after import and processing.

Changes:

- Centralize `days` state in `App`.
- Refresh days when import completes, when `api.run()` succeeds, and when the SSE task snapshot transitions from active to idle.
- If a selected day is still valid, keep it selected and refresh its sessions.
- This must fix the current Playwright failure where processing starts but no `2087-...` day button appears without reload.

### Task Diagnostics

Failed tasks must expose enough information for an operator to act.

Changes:

- Render `last_error`, attempts, and target identifiers in `TaskList`.
- Retry action calls `/retry` and then `/run`.
- Keep visual density appropriate for repeated operational use.

### Accessibility and Responsive Layout

Interactive controls must be keyboard reachable.

Changes:

- Convert clickable LLM candidate rows to real buttons or add equivalent role/tab/key semantics. Prefer real buttons.
- Give toasts explicit close buttons and error-appropriate ARIA roles.
- Add responsive CSS breakpoints so the three-column layout collapses cleanly on narrow viewports.
- Ensure buttons and labels wrap without overlapping.

### Audio Playback Feedback

Audio playback failures should not be silent.

Changes:

- Check audio response status before decoding/playing.
- Surface playback failures through the same toast or inline error mechanism.
- In tests, stub media playback so Vitest does not emit jsdom `HTMLMediaElement.play` noise.

## Layer 4: Efficiency Improvements

### Audio Chunk Memory

Reduce memory pressure before broad model-level optimization.

Changes:

- Lower production default `max_chunk_ms` from 900,000 ms to 120,000 ms.
- Replace the IEEE-float fallback's whole-file read with metadata scanning plus direct data-chunk slicing for the requested range.
- Convert PCM/float chunks in bounded blocks so conversion memory is proportional to the configured block size, not to the whole source file or whole speech range.

### Model Startup

The command-adapter FunASR path currently reloads models per task. This is a major efficiency limit for many chunks.

This design defers a daemon/batch model server until the production safety baseline is green. The later design should compare:

- A batch wrapper that accepts multiple chunks per process.
- A local long-running daemon behind the existing ASR/VAD ports.
- Keeping command adapters as the simple fallback path.

## Testing Strategy

Use test-first implementation for behavior changes.

Targeted Python tests:

- Config default and path resolution tests.
- Mock-vs-rule-based LLM construction tests.
- CLI `--mock` tests proving mock remains explicit.
- Task retry reset tests.
- Command adapter timeout tests.
- Archive cleanup path/hash safety tests.
- Launchd plist rendering tests for absolute `uv`, PATH, log directory, and source discovery behavior.
- File import collision tests.
- Docker ignore or Dockerfile tests where existing test style supports it.

Targeted Web tests:

- Bootstrap API failure renders an actionable error.
- Import/run refreshes days after processing state changes.
- Task list shows `last_error` and retry starts the worker.
- LLM candidate rows are keyboard reachable.
- Toast close is a real button.
- Responsive layout smoke tests where existing test tools support it.
- Media playback is stubbed cleanly.

End-to-end verification:

```bash
uv run pytest -q
cd web && npm test
cd web && npm run build
cd web && npm run e2e
```

Operational smoke checks:

```bash
uv run pcn health --config config/local.example.toml
uv run pcn doctor --config config/local.example.toml
uv run pcn launchd-write-plists --config config/local.example.toml --output-dir /tmp/pcn-launchd-check
plutil -lint /tmp/pcn-launchd-check/*.plist
curl -f http://127.0.0.1:8765/api/health
curl -f http://127.0.0.1:8765/app/
```

Docker checks where available:

```bash
docker compose config
DOCKER_BUILDKIT=1 docker compose build --progress=plain
```

## Review Loop

After each coherent implementation batch:

1. Run the relevant targeted tests.
2. Run full Python and Web unit/build checks.
3. Dispatch three independent read-only subagent reviews:
   - Backend/data safety.
   - Web usability/accessibility.
   - Operations/performance.
4. Fix concrete findings.
5. Repeat the three-review loop until the agents return no actionable production-readiness issues.

Subagents must not edit files during review rounds. The main thread integrates fixes and owns final verification.

## Definition of Done

The goal is not complete until current evidence proves all of the following:

- Core local workflow runs without requiring an LLM key or LLM command.
- Mock fixture LLM output cannot enter production data by default.
- Manual retry is immediate and visible.
- External command hangs are bounded by timeouts.
- Archive cleanup cannot delete an unchecked path.
- Config paths resolve consistently.
- Launchd plists are self-contained enough for login startup.
- One-command Web startup serves the control panel or fails clearly.
- Web control panel shows API errors, refreshes navigation after import/run, exposes task diagnostics, and works on narrow viewports.
- Test suites and build checks pass.
- Live or deterministic e2e verifies the import/run/review path.
- Multiple rounds of three independent subagent review find no remaining actionable issues.

## Implementation Order

1. Layer 1 production safety.
2. Layer 2 operations startup.
3. Layer 3 Web usability.
4. Layer 4 low-risk efficiency.
5. Three-review loop until clean.

This order intentionally prioritizes data integrity and recoverability before visual polish or model-level performance.
