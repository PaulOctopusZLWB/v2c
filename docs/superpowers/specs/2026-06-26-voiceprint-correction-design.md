# Voiceprint Correction Design

Date: 2026-06-26

## Decision

Implement two targeted correction tools on the voiceprint map:

1. **取消识别** on a person legend item: clear the selected person attribution from the current map group and return those segments to 未识别.
2. **邻域纠偏** on the map toolbar: preview and apply a local-neighbour smoothing pass that fixes isolated automatic misidentifications inside a strong surrounding person cluster.

The tools correct attribution noise. They do not delete person records, rewrite transcript text, or re-cluster every voiceprint from scratch.

## Boundaries

- Use stored CAM++ embedding vectors for correction decisions, not the rendered 2D UMAP/PCA coordinates.
- Do not overwrite `source='manual'` segment labels. Manual lasso labels and explicit confirmations remain ground truth.
- The automatic smoothing pass may only update `source='voiceprint'` attributions.
- The legend-level cancellation clears only the segments visible in the current projection group. It does not call `DELETE /api/persons/{id}`.
- Existing projection caches must be cleared after any attribution mutation so the map recolors immediately.
- The UI must show a preview before applying neighbourhood smoothing because it can affect many segments.

## User Experience

### Legend-Level Cancellation

In person color mode, each non-未识别 legend item gets a compact delete/cancel button.

Clicking it opens a confirmation for the current group:

```text
取消“胡家豪”的识别归属？
将 2 段回到未识别，不会删除人物档案。
```

Confirming calls the backend with the segment ids currently in that legend group. On success, the map refetches the projection and the People panel can refresh attribution counts.

### Neighbourhood Correction

The map toolbar gets a `邻域纠偏` action. The flow has two phases:

1. Preview: backend computes candidate corrections and returns a summary grouped by `from_person -> to_person`, plus totals.
2. Apply: user confirms the preview, backend writes the same correction plan if it is still valid enough for the current scope.

The preview should make the blast radius visible, for example:

```text
将纠正 7 段自动识别：
胡家豪 -> 肖俊：2
张望舒 -> 我：1
未识别 -> 王东亮：4
```

If there are no safe corrections, the UI reports that no isolated misidentifications were found.

## Algorithm

Neighbourhood correction is a local majority vote over normalized embeddings:

1. Load in-scope embedded active segments.
2. Load current person attribution and attribution source for those segments.
3. Exclude manually labelled segments from mutation candidates.
4. For each candidate with a finite embedding, compute cosine similarity to other in-scope embedded segments.
5. Take the top `k` neighbours above a similarity floor.
6. Count neighbours by current person id, excluding unknown labels unless the candidate is currently assigned and the neighbourhood is mostly unknown.
7. Propose a correction when:
   - neighbour count is at least `min_neighbours`;
   - one person holds at least `majority_ratio` of the usable neighbours;
   - the proposed person differs from the candidate's current person;
   - the best neighbour similarity clears `similarity_floor`;
   - the candidate attribution source is not `manual`.
8. Apply writes `segment_person_overrides` with `source='voiceprint'` for person assignments and deletes voiceprint overrides when the correction returns a segment to 未识别.

Default parameters:

- `k = 15`
- `min_neighbours = 8`
- `majority_ratio = 0.75`
- `similarity_floor = 0.35`

These defaults are conservative for the observed "large cluster with one or two wrong colours" pattern.

## API

Add backend service functions:

- `clear_segment_person_attributions(config, segment_ids) -> {"cleared": int}`
- `preview_neighbor_corrections(config, scope, params) -> NeighborCorrectionPreview`
- `apply_neighbor_corrections(config, scope, params) -> NeighborCorrectionResult`

Add routes:

- `POST /api/people/clear-segment-attributions`
- `POST /api/people/neighbor-correction/preview`
- `POST /api/people/neighbor-correction/apply`

The preview and apply routes use the same scope shape as projection where practical: `session_ids`, `days`, and correction parameters. The first implementation may support a single current projection request body and reuse the backend scope helper used by the projection endpoint.

## Components

- `src/personal_context_node/speaker_embeddings.py`
  - implements embedding-neighbour correction and clear-attribution mutation.
- `src/personal_context_node/web/routes_speakers.py`
  - validates payloads and exposes routes.
- `web/src/api/client.ts`
  - adds typed API calls.
- `web/src/api/types.ts`
  - adds correction preview/result types.
- `web/src/features/viz/VoiceprintMap.tsx`
  - renders legend cancel buttons and the toolbar correction flow.
- `web/src/App.tsx`
  - wires mutation callbacks and refreshes related panels.
- `web/src/styles.css`
  - adds compact controls without changing map layout geometry.

## Error Handling

- Empty segment ids return `cleared: 0`.
- Unknown segment ids are ignored unless no provided ids are valid.
- Missing embeddings produce a 400 response for neighbourhood correction.
- If no labelled neighbours are strong enough, preview returns zero changes rather than forcing weak guesses.
- Manual labels are reported as skipped when relevant.
- Backend clears projection caches only after successful mutations.
- Frontend disables the relevant action while a request is running and surfaces backend errors in the map area.

## Testing

Backend tests:

- clearing selected segment attributions leaves person rows and unrelated attributions intact;
- clearing clears projection cache;
- neighbour preview fixes isolated automatic mislabels in a synthetic embedding cluster;
- neighbour correction does not mutate manual labels;
- apply writes the previewed voiceprint overrides and returns grouped counts;
- weak or ambiguous neighbour evidence produces no changes.

Frontend tests:

- person legend renders a cancel button for assigned groups but not 未识别;
- clicking cancel calls the clear API with the group's segment ids and refetches projection;
- `邻域纠偏` calls preview, displays grouped changes, and only applies after confirmation;
- zero-change preview produces a non-destructive message;
- request failures surface an error and leave current points displayed.

Manual verification:

- run targeted Python tests for speaker embedding/API paths;
- run targeted Vitest tests for `VoiceprintMap`;
- run `npm run build`;
- visually check the speakers tab at `http://localhost:8765/app/#tab=speakers` if a dev server is available.

## Non-Goals

- Do not remove person records from the People database.
- Do not change diarization `speaker_cluster_id` values.
- Do not make neighbourhood correction fully automatic on page load.
- Do not replace the existing global auto-attribute flow.
- Do not tune clustering thresholds from the UI in the first pass.
