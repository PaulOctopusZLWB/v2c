# Web Config Page (model settings + speaker-cluster management) — Implementation Plan

> REQUIRED SUB-SKILL: subagent-driven-development. TDD per task; commit by feature.

**Goal:** A small web 配置页 to (1) choose ASR mode + LLM model (and GLM endpoint/thinking/preset_spk_num) and (2) manage 声纹聚类 — list a day's clusters and bulk-merge over-clustered `spk_NN` into one person.

**Key decisions (confirmed):** settings persist in a DB `settings` table and take effect on the NEXT run (no restart): the worker re-reads overrides each drain (ASR via `config.model_copy(update=...)`; GLM via `os.environ` before the drain, inherited by the wrapper subprocess). `GLM_API_KEY` stays env-managed, never web-editable. Speaker "merge" reuses the existing assign-cluster→person model (attribution view already collapses many clusters → one person); we add a list endpoint + a bulk-assign endpoint + UI.

**Allow-list (only these are web-settable):** `asr_mode` ∈ {chunk,diarize}, `asr_preset_spk_num` (positive int | null), `glm_model` (str), `glm_base_url` (str), `glm_thinking` (bool).

---

## Backend A — settings store + runtime apply + /api/settings
- **Storage:** add `create table if not exists settings (key text primary key, value text not null, updated_at text not null default '')` to the SCHEMA constant in `storage/sqlite.py`; add `get_settings(conn)->dict[str,str]` / `put_setting(conn,key,value)` near `fetch_all`. New module `src/personal_context_node/settings.py`: `read_overrides(config)` returns the validated, allow-listed override dict (typed: asr_mode/asr_preset_spk_num/glm_*); `write_settings(config, updates)` validates + persists; `effective_settings(config)` merges overrides over config/env defaults (for the GET).
- **Worker apply:** in `worker._drain_to_completion`, BEFORE `build_pipeline_adapters`: read overrides; build `effective = self._config.model_copy(update={asr_mode, asr_preset_spk_num})`; set `os.environ` for present `glm_model→GLM_MODEL`, `glm_base_url→GLM_BASE_URL`, `glm_thinking→GLM_THINKING`. Use `effective` for the adapter build. (drain_now + _run + _import_then_drain all route through `_drain_to_completion`.)
- **Routes:** `web/routes_settings.py` `APIRouter(prefix="/api/settings")`: `GET ""` → `effective_settings(app.state.config)`; `PUT ""` → pydantic `SettingsUpdate` (all optional, allow-listed, validated), persist via `write_settings`, return the new effective settings. Register in `app.py`.
- **TDD:** put_setting/get_settings round-trip; read_overrides validates (bad asr_mode rejected, preset_spk_num int|null, glm_thinking bool); effective merge (override > env > default); worker uses overrides on the next drain (a fake build captures the effective asr_mode/preset and that GLM_MODEL env was set); GET/PUT API round-trip + rejects an unknown/invalid key.

## Backend B — speaker cluster list + bulk assign (mostly surfacing existing)
- **GET `/api/speakers/clusters?day=YYYY-MM-DD`** (add to `routes_speakers.py`): one GROUP BY over `transcript_segments` for the day → per cluster: `speaker_cluster_id`, `person_id`+`person_label` (LEFT JOIN speaker_mappings), `segment_count`, `total_speech_ms`, `sample_segment_id`+`sample_text` (the longest segment). Reuse the day-scoped query shape in `speaker_review.py`.
- **POST `/api/speakers/assign-person-bulk`** `{speakers:[str], person_id:str}`: loop the existing `_upsert_speaker_mapping`/assign path for each speaker → one person (this IS the merge; attribution collapses them). Returns count.
- **TDD:** clusters list returns per-cluster stats + sample for a seeded diarized day; bulk-assign maps N clusters to one person and `v_segment_attribution` collapses them to one person_id; empty/unknown person rejected.

## Frontend — Settings + 声纹聚类 panels
- `api/types.ts`: `Settings` (asr_mode, asr_preset_spk_num, glm_model, glm_base_url, glm_thinking), `SpeakerCluster` (cluster_id, person_id?, person_label?, segment_count, total_speech_ms, sample_segment_id, sample_text).
- `api/client.ts`: `settings()`, `updateSettings(body)`, `speakerClusters(day)`, `assignPersonBulk(speakers, person_id)`.
- `components/SettingsPanel.tsx`: model settings form (ASR mode select chunk/diarize; LLM model input/select with glm-5.1 + glm-4-flash presets; GLM base URL; thinking checkbox; preset_spk_num number) → `updateSettings`; shows "下次运行生效" hint. Reuse `useAsyncAction`, `.section-title`/`.card`.
- `components/ClusterPanel.tsx` (or a section): list `speakerClusters(day)` rows with sample text + segment count + current person; checkboxes to select multiple clusters; a person picker + "合并/指派为同一人" → `assignPersonBulk`; reuse `useSegmentAudio` on `sample_segment_id` for cluster-level "听样本".
- Slot both into `App.tsx` (a 配置 area / right-rail panel); add i18n strings.
- **TDD (Vitest):** SettingsPanel renders current settings + saving calls updateSettings; ClusterPanel lists clusters and a bulk-merge calls assignPersonBulk with the selected speakers + person.

## Review
After build: triple-independent review + adversarial verification, loop until clean (the established harness).
