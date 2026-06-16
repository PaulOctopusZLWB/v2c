# Speaker Diarization (词级/W2) + Per-Speaker Analytical Summary + Configurable Reasoning LLM — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Follow TDD: write the failing test, run it red, implement, run it green, commit. One feature per commit.

**Goal:** Produce a per-speaker analytical daily/session summary (每个人的 观点/情绪/倾向/潜在需求 + 全场核心结论) — powered by true per-sentence speaker diarization (发言人 1/2/3) and a configurable reasoning GLM model.

**Architecture (locked decisions):**
- **W2 transcription path** — a NEW opt-in ASR mode `diarize` that runs a **whole-file** FunASR Paraformer pass (`model=paraformer-zh, vad_model=fsmn-vad, punc_model=ct-punc, spk_model=cam++, spk_mode=punc_segment, device=mps`) yielding `sentence_info` with `{text, start, end, spk, timestamp}` per sentence. One pass produces text + speaker + absolute timestamps, globally clustered, so speaker ids are stable across the file. Kept fast by a **resident Paraformer daemon** (load once, MPS) mirroring the existing `PersistentCommandASRAdapter`. The existing per-chunk SenseVoice path (`asr_mode="chunk"`, default) is **left intact**; diarization is opt-in via config.
- Because diarized ASR is **per audio_file** (not per chunk), in `diarize` mode the pipeline edge is `import→transcribe_diarize→session_derive`, and the day-gate fan-in counts per-audio_file completion (NOT per-chunk). The chunk/VAD stages are unused in this mode.
- `transcript_segments.speaker` and `.speaker_cluster_id` get `spk_01/spk_02/…` (zero-padded by first appearance); single-speaker file → both columns `"self"` (preserves the default-self prior). **Both columns always equal** (the review path joins on `speaker`, the attribution view on `speaker_cluster_id`; they must not diverge). The entire downstream (speaker_clusters / speaker_mappings / 声纹审阅 / SpeakerPanel / v_segment_attribution) consumes cluster ids unchanged.
- **Per-speaker summary** is a NEW LLM contract: `headline + core_conclusions[] + per_speaker[{speaker_cluster_id, viewpoints[{text,evidence_refs}], sentiment, stance, latent_needs[]}] + open_questions[]`. New dataclasses in `core/ports/llm.py`, new validators in `adapters/llm/command.py`, new GLM prompt + normalize, new note rendering, new review surface. All text 简体中文.
- **Configurable reasoning model**: `GLM_MODEL` (default `glm-4-flash`) + new `GLM_THINKING` env → request body `thinking:{type:"enabled"}` + `clear_thinking:false`, parse `reasoning_content`, longer timeout. Default stays `glm-4-flash` until the user recharges for `glm-5.2`.

**Tech Stack:** Python 3.11+, uv (`uv run pytest -q`), FunASR 1.3.9 (Paraformer-zh + ct-punc + cam++), torch 2.12 MPS, Pydantic v2, FastAPI, SQLite (WAL), React 18 + Vitest. Ports-and-adapters; `core/**` must not import adapters/funasr/pyannote (enforced by `tests/test_architecture.py`).

**Models to pre-seed (one-time, needs network):**
- `iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` (~1GB, Paraformer-zh w/ timestamps)
- `iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch` (ct-punc)
- `iic/speech_campplus_sv_zh-cn_16k-common` (~28MB, CAM++)
(fsmn-vad + SenseVoice already cached.)

---

## File Structure

**Create:**
- `scripts/funasr_paraformer_diarize_wrapper.py` — whole-file Paraformer+punc+cam++ wrapper; one-shot + `--server` resident modes; emits `{"segments":[{text,start_ms,end_ms,speaker,confidence,language}]}` (speaker = `spk_NN` or `self`). Mirrors `funasr_sensevoice_wrapper.py`.
- `src/personal_context_node/core/ports/diarized_asr.py` — (only if a distinct port is needed; otherwise reuse `ASRPort` extended with a per-file `transcribe_file`). See Task A2.
- `tests/test_funasr_paraformer_diarize_wrapper.py`
- `tests/test_diarized_transcription.py`
- `tests/test_per_speaker_summary_contract.py`

**Modify:**
- `src/personal_context_node/core/ports/asr.py` — add `speaker` to `ASRSegment`.
- `src/personal_context_node/adapters/asr/command.py` + `persistent_command.py` — parse `speaker`; add a per-file `transcribe_file(audio_path)` variant (or a sibling adapter) for the whole-file diarize pass.
- `src/personal_context_node/pipeline_adapters.py` — `build_asr` supports `asr_mode="diarize"` (paraformer wrapper, default `--server`); thread through `build_pipeline_adapters`.
- `src/personal_context_node/config.py` + `config/funasr.example.toml` — `[asr].mode`, `[asr].diarize_model`/`punc_model`/`spk_model`/`spk_mode`/`preset_spk_num`.
- `src/personal_context_node/transcription.py` — a per-file diarized transcribe that writes speaker-labeled segments; `spk_NN` mapping + `"self"` fallback; replace the `:75` hardcode.
- `src/personal_context_node/process_runner.py` — in `diarize` mode, `import→transcribe_diarize` edge, `transcribe_diarize` dispatch, per-file day-gate fan-in.
- `src/personal_context_node/tasks.py` — `ALLOWED_TASK_TYPES` += `transcribe_diarize`.
- `scripts/glm_llm_wrapper.py` — per-speaker prompt + normalize; `GLM_THINKING` thinking param + `reasoning_content`.
- `src/personal_context_node/core/ports/llm.py` — per-speaker `SessionSummary`/`DailyContext` fields (new dataclasses `SpeakerAnalysis`).
- `src/personal_context_node/adapters/llm/command.py` — validators for per-speaker fields.
- `src/personal_context_node/llm_processing.py` + `session_summaries.py` — group transcript by `speaker_cluster_id` when building the LLM payload.
- `src/personal_context_node/obsidian_publish.py` (+ templates) — render per-speaker sections.
- `web/src/` — surface per-speaker viewpoints in the review UI.

---

## Epic 0 — Model seeding & wrapper smoke (de-risk first)

### Task 0.1: Pre-seed the diarization models + prove the whole-file pass on MPS
**Files:** none committed (a throwaway smoke script under `.tmp/`).
- [ ] **Step 1:** Write `.tmp/diar_smoke.py` that runs `AutoModel(model='paraformer-zh', vad_model='fsmn-vad', punc_model='ct-punc', spk_model='cam++', spk_mode='punc_segment', device='mps')` on a sample WAV and prints `sentence_info` (text/start/end/spk) + total wall time, with `PYTORCH_ENABLE_MPS_FALLBACK=1`. Use a 2-speaker clip if available; else any sample.
- [ ] **Step 2:** Run it. Expected: first run downloads ~1GB models, then prints sentences with integer `spk`. Confirm MPS used and it completes. Record load time + per-file time.
- [ ] **Step 3:** If MPS errors on an op, confirm the FALLBACK env lets it finish on CPU; note any op that falls back. If the model can't load at all, STOP and escalate (the plan assumes Paraformer+cam++ works here).
- [ ] **Step 4:** Delete `.tmp/diar_smoke.py`. No commit (research artifact). Record findings in the task notes.

> Gate: do not proceed to A-tasks until 0.1 proves the whole-file Paraformer+cam++ pass produces per-sentence `spk` on this machine.

---

## Epic A — W2 diarized transcription path

### Task A1: `ASRSegment` gains a `speaker` field
**Files:** Modify `src/personal_context_node/core/ports/asr.py`; Test `tests/test_asr_port.py` (create if absent).
- [ ] **Step 1 (red):** Test that `ASRSegment(text=..., start_ms=0, end_ms=1, speaker="spk_01")` exposes `.speaker`, and that it defaults to `"self"` when omitted (back-compat for the SenseVoice path).
- [ ] **Step 2:** Run red.
- [ ] **Step 3 (green):** Add `speaker: str = "self"` to the `ASRSegment` frozen dataclass.
- [ ] **Step 4:** Run green. Confirm existing ASR tests still pass (default keeps them valid).
- [ ] **Step 5:** Commit `feat(asr): add speaker field to ASRSegment (defaults to self)`.

### Task A2: Whole-file Paraformer diarize wrapper — protocol + parsing (TDD with a fake model)
**Files:** Create `scripts/funasr_paraformer_diarize_wrapper.py`; Test `tests/test_funasr_paraformer_diarize_wrapper.py`.
Mirror `funasr_sensevoice_wrapper.py` (argparse, `resolve_device` MPS fallback, `redirect_stdout(sys.stderr)` around model work, exit codes 3=terminal/2=retryable, `--server` resident loop). The wrapper's pure functions take an injected fake model so tests need no real FunASR.
- [ ] **Step 1 (red):** Test a `normalize_diarized(sentence_info)` function: given `[{"text":"<|zh|>你好","start":0,"end":1000,"spk":0,"timestamp":[[0,1000]]},{"text":"在","start":1000,"end":1500,"spk":1}]` it returns segments `[{text:"你好",start_ms:0,end_ms:1000,speaker:"spk_01",...},{text:"在",start_ms:1000,end_ms:1500,speaker:"spk_02",...}]` — integer spk mapped to `spk_NN` zero-padded by FIRST APPEARANCE order (0→spk_01, 1→spk_02), tags stripped, language carried.
- [ ] **Step 2 (red):** Test the single-speaker collapse: if every sentence has the same `spk` (or spk absent), all segments get speaker `"self"` (not `spk_01`).
- [ ] **Step 3 (red):** Test `run_server(fake_model, stdin, stdout)`: one audio path per input line → one JSON result line `{"segments":[...]}`; a missing file path → `{"error":..., "terminal":true}` (mirror the SenseVoice server terminal channel).
- [ ] **Step 4:** Run red (function/file absent).
- [ ] **Step 5 (green):** Implement the wrapper: argparse (`audio_path?`, `--model paraformer-zh`, `--vad-model fsmn-vad`, `--punc-model ct-punc`, `--spk-model cam++`, `--spk-mode punc_segment`, `--preset-spk-num`, `--device mps`, `--language zh`, `--server`); `normalize_diarized`; `run_server`; `main` one-shot path. Model construction adds vad/punc/spk kwargs; `model.generate(...)` → `sentence_info`. All FunASR import/load/generate inside `redirect_stdout(sys.stderr)`.
- [ ] **Step 6:** Run green.
- [ ] **Step 7:** Commit `feat(asr): paraformer whole-file diarize wrapper (text+speaker+timestamps)`.

### Task A3: Per-file diarized ASR adapter (resident daemon)
**Files:** Modify `src/personal_context_node/adapters/asr/persistent_command.py` (or a sibling `PersistentDiarizeAdapter`); Test extend `tests/test_persistent_command_asr.py`.
- [ ] **Step 1 (red):** Test that a per-file adapter `transcribe_file(audio_path)` returns an `ASRResult` whose segments carry `speaker` (`spk_01/spk_02`), driven by a fake server script that echoes the diarized JSON; assert the resident process is reused across two files (process-identity, like the existing load-once anchor).
- [ ] **Step 2 (red):** Terminal-flag test: a `{"error",...,"terminal":true}` line → `TerminalPortError` (reuse existing pattern).
- [ ] **Step 3:** Run red.
- [ ] **Step 4 (green):** Implement: the adapter writes the audio_file path, reads one JSON line, parses segments incl. `speaker`. Reuse `_readline_with_timeout`, `close()`, terminal-channel logic from the existing `PersistentCommandASRAdapter`.
- [ ] **Step 5:** Run green. Commit `feat(asr): resident per-file diarize adapter`.

### Task A4: `build_asr` / config — `asr_mode="diarize"`
**Files:** Modify `pipeline_adapters.py`, `config.py`, `config/funasr.example.toml`; Test extend `tests/test_pipeline_adapters.py`, `tests/test_config.py`.
- [ ] **Step 1 (red):** Test `build_asr(asr_backend="funasr_server", asr_mode="diarize", asr_device="mps", ...)` returns the per-file diarize adapter with the full argv `[..., funasr_paraformer_diarize_wrapper.py, --server, --model, <m>, --vad-model, fsmn-vad, --punc-model, ct-punc, --spk-model, cam++, --spk-mode, punc_segment, --device, mps, --language, zh]` (pin the WHOLE argv — round-6 lesson).
- [ ] **Step 2 (red):** Test `from_toml` propagates `[asr].mode` and the diarize model knobs.
- [ ] **Step 3:** Run red. **Step 4 (green):** add `asr_mode` + diarize knobs to config + build_asr dispatch. **Step 5:** green. Commit `feat(asr): config asr_mode=diarize wires the paraformer diarize adapter`.

### Task A5: Per-file diarized transcribe — write speaker-labeled segments
**Files:** Modify `src/personal_context_node/transcription.py` (add `transcribe_audio_file_diarized`); Test `tests/test_diarized_transcription.py`.
- [ ] **Step 1 (red):** Seed an audio_file; with a fake diarize adapter returning 3 segments speakers `[spk_01, spk_02, spk_01]`, call the new per-file transcribe and assert: 3 `transcript_segments` rows with `speaker`/`speaker_cluster_id` = `spk_01/spk_02/spk_01` (both columns equal), absolute ms from the diarizer, `is_active=1`; AND a `speaker_clusters` row per distinct cluster (`source_type='diarization'`, `source_ref=audio_file_id`) created.
- [ ] **Step 2 (red):** Single-speaker file → all segments `"self"`, no `spk_01`; the existing default-self attribution still holds.
- [ ] **Step 3 (red):** Re-run safety: calling twice deactivates+reinserts (no duplicate active segments), mirroring `transcribe_pending_chunks`.
- [ ] **Step 4:** Run red. **Step 5 (green):** implement `transcribe_audio_file_diarized(config, asr, audio_file_id)`: run adapter on `local_raw_path`; build first-appearance `spk_map`; upsert `speaker_clusters`; deactivate old + insert segments with speaker label into both columns; `"self"` when one cluster. **Step 6:** green. Commit `feat(transcribe): per-file diarized transcription writes speaker clusters`.

### Task A6: Pipeline wiring — `transcribe_diarize` stage (diarize mode)
**Files:** Modify `process_runner.py`, `tasks.py`; Test `tests/test_process_runner_diarize.py`.
- [ ] **Step 1 (red):** With `asr_mode="diarize"`, after import enqueues the per-file stage, `process_once` claims `transcribe_diarize` for the audio_file and (via mock adapters) produces speaker-labeled segments, then fans in to `session_derive` for the file's day. Assert the day reaches `session_derive` only after ALL same-day files' `transcribe_diarize` succeed (reuse the round-7 whole-day gate, now per-audio_file).
- [ ] **Step 2 (red):** A second same-day file still pending → day NOT ready (the round-7 invariant, re-expressed for per-file ASR).
- [ ] **Step 3:** Run red. **Step 4 (green):** add `transcribe_diarize` to `ALLOWED_TASK_TYPES` + `PROCESS_TASK_ORDER`; in diarize mode use the `import→transcribe_diarize→session_derive` edges; add the dispatch branch calling `transcribe_audio_file_diarized`; update `_ready_session_derive_dates_in_conn` to count per-audio_file diarize completion when in diarize mode (terminal-failed file = done, like round-7). Thread the diarize adapter through `process_once`/`drain_process_queue`/`worker.py`. **Step 5:** green. Commit `feat(scheduler): transcribe_diarize stage (per-file) with whole-day fan-in`.

### Task A7: Diarize-mode e2e (mock models, no network)
**Files:** Test `tests/test_diarize_e2e.py`.
- [ ] **Step 1 (red):** Full drain in diarize mode with mock VAD/diarize/LLM over 2 same-day files (one 2-speaker, one 1-speaker): assert sessions derive once, segments carry `spk_01/spk_02` for the multi-speaker file and `self` for the single, the day publishes exactly once, and `v_segment_attribution` / speaker_review lists the clusters. **Step 2:** red. **Step 3 (green):** fix wiring gaps. **Step 4:** green. Commit `test(diarize): multi-file multi-speaker e2e`.

---

## Epic B — Per-speaker analytical summary

### Task B1: New LLM contract dataclasses
**Files:** Modify `core/ports/llm.py`; Test `tests/test_llm_ports.py`.
- [ ] **Step 1 (red):** Test a `SpeakerAnalysis(speaker_cluster_id, viewpoints: list[MemoryCandidate-like {text,evidence_refs}], sentiment: str, stance: str, latent_needs: list[str])` and that `SessionSummary` gains `core_conclusions: list[str]` + `per_speaker: list[SpeakerAnalysis]` (keep existing fields for back-compat / default empty).
- [ ] **Step 2:** red. **Step 3 (green):** add the dataclasses/fields (frozen, defaults so existing rule_based path stays valid). **Step 4:** green. Commit `feat(llm): per-speaker analysis fields on the summary contract`.

### Task B2: CommandLLMAdapter validators for per-speaker fields
**Files:** Modify `adapters/llm/command.py`; Test extend `tests/test_command_llm.py`.
- [ ] **Step 1 (red):** A wrapper payload with `per_speaker:[{speaker_cluster_id, viewpoints:[{text,evidence_refs}], sentiment, stance, latent_needs}]` + `core_conclusions:[...]` validates into `SessionSummary`; missing required per-speaker field → `TerminalPortError`; a malformed `per_speaker` (dict not list, non-dict item) is tolerated as empty (mirror the round-7 `_as_list` hardening). **Step 2:** red. **Step 3 (green):** add `_speaker_analysis` validator + wire into `generate_session_summary`. **Step 4:** green. Commit `feat(llm): validate per-speaker analysis in the adapter contract`.

### Task B3: GLM wrapper — per-speaker prompt + normalize + group transcript by speaker
**Files:** Modify `scripts/glm_llm_wrapper.py`, `llm_processing.py`/`session_summaries.py`; Test extend `tests/test_glm_llm_wrapper.py`.
- [ ] **Step 1 (red):** `normalize_session_summary` produces `core_conclusions` + `per_speaker` (each speaker's viewpoints filtered to known evidence ids, sentiment/stance strings, latent_needs list), dropping malformed items (no crash). The transcript fed to GLM is grouped/labeled by `speaker_cluster_id`. All text fields Chinese (prompt directive). **Step 2:** red. **Step 3 (green):** new `build_session_messages` prompt asking for the per-speaker schema in 简体中文 (atomic viewpoints, evidence-cited); `normalize_session_summary` builds the new shape with `_as_list`/isinstance guards. Group segments by speaker when building the user prompt. **Step 4:** green; plus a live-stub e2e through `CommandLLMAdapter` (the round-5 authoritative-contract test). Commit `feat(llm): GLM per-speaker analytical session summary (Chinese)`.

### Task B4: Render per-speaker sections in the daily/session note
**Files:** Modify `obsidian_publish.py` (+ template); Test extend the publish tests.
- [ ] **Step 1 (red):** A published note contains a per-speaker block per cluster (观点/情绪/倾向/潜在需求) + a 核心结论 list, inside managed `pcn:block` markers (idempotent re-publish). **Step 2:** red. **Step 3 (green):** render. **Step 4:** green. Commit `feat(publish): per-speaker analysis sections in the daily note`.

### Task B5: Surface per-speaker viewpoints in the review UI
**Files:** Modify `web/src/` (+ `api/types.ts`); Test extend `web/src/__tests__/`.
- [ ] **Step 1 (red):** A Vitest test that, given a session with `per_speaker` data, the review panel renders each speaker's viewpoints with their evidence and the 核心结论 — and a regression (dropping a field) fails (mutation-grade, round-6 lesson). **Step 2:** red. **Step 3 (green):** render + types. **Step 4:** green; `cd web && npm test && npm run build`. Commit `feat(web): per-speaker viewpoints in the review panel`.

---

## Epic C — Configurable reasoning model

### Task C1: GLM thinking/effort + reasoning_content
**Files:** Modify `scripts/glm_llm_wrapper.py`; Test extend `tests/test_glm_llm_wrapper.py`.
- [ ] **Step 1 (red):** With `GLM_THINKING=enabled`, `call_glm` request body includes `thinking:{type:"enabled"}` and `clear_thinking:false`; the body still has `response_format`/`model`; when the response carries `reasoning_content`, only `content` is parsed as the JSON result (reasoning is ignored for the contract). With `GLM_THINKING` unset, no `thinking` key (back-compat; temperature 0.2 pinned test stays green). **Step 2:** red. **Step 3 (green):** read `GLM_THINKING` env, add the body keys conditionally, lengthen the urlopen timeout when thinking is on. **Step 4:** green. Commit `feat(llm): optional GLM deep-thinking (effort) via GLM_THINKING env`.
- [ ] **Step 5 (doc):** Note in the config/runbook: set `GLM_MODEL=glm-5.2 GLM_THINKING=enabled` after recharging the bigmodel.cn balance (the paid models currently return 1113 余额不足). Commit `docs(llm): GLM-5.x reasoning model opt-in`.

---

## Risks / Decisions
1. **~1GB model download (Task 0.1 gate).** Paraformer + ct-punc must download on first run; pure-offline boxes fail until pre-seeded. The smoke task de-risks load + MPS before any build.
2. **`hdbscan` missing** — only used for >~25min single-file speech; pass `preset_spk_num` / rely on SpectralCluster for normal files; add `hdbscan` to deps only if long recordings need it. Note: <20 sub-chunks → CAM++ returns a single speaker (fine: collapses to `self`).
3. **Two join strategies invariant** — `speaker` and `speaker_cluster_id` MUST stay equal (review path joins `speaker`, attribution joins `speaker_cluster_id`). Tests assert both columns equal.
4. **Throughput** — diarize mode replaces the SenseVoice per-chunk daemon with a per-file Paraformer daemon (load once, MPS). Per-file whole-pass is heavier than per-chunk SenseVoice; acceptable given the daemon amortizes load. `asr_mode="chunk"` remains the fast non-diarized default.
5. **Cost/billing** — per-speaker reasoning summaries on `glm-5.2` need a paid balance; default `glm-4-flash` keeps the pipeline runnable until recharge.
6. **Scope** — this is a large, multi-epic build comparable to the throughput-and-glm branch; expect a full subagent-driven-development cycle with spec+quality review per task, then a triple-review pass before merge.

## Self-Review (writing-plans checklist)
- Spec coverage: diarization (A), per-speaker summary (B), reasoning model (C), model seeding (0) — all four user requirements mapped. ✓
- Type consistency: `ASRSegment.speaker`, `SpeakerAnalysis`, `spk_NN`/`self`, `asr_mode` used consistently across tasks. ✓
- No placeholders: each task has red→green→commit TDD steps with concrete assertions; novel code (wrapper, transcription mapping, per-speaker schema) specified; boilerplate references the exact mirror file. ✓
