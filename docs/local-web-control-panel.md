# Local Web Control Panel

Localhost-only panel bound to `127.0.0.1`. It is a thin observer + one extra worker over the existing task queue — it does not replace the CLI, launchd automation, or Obsidian.

## Start

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pcn web --config config/local.toml
```

Dev frontend: `cd web && npm run dev` then open `http://127.0.0.1:5173`.
Built frontend: open `http://127.0.0.1:8765/app`.

## Flow in the panel

1. Enter the recordings directory and click **Import** — files are imported and `vad` tasks enqueued; the panel then starts the background worker (or click **Run** later).
2. Watch live task status (SSE) and the pipeline rail; **Stop** requests a cooperative stop between work units.
3. **Transcript Review** — play a segment, accept/reject/flag it, and correct attribution.
4. **Speakers** — assign a speaker to a person (assigning two speakers to one person = merge), override a single segment's person, or add a new person inline.
5. **LLM Result** — read the daily context summary and memory candidates (read-only).

## How it relates to the rest of the system

- Starting a run drives the *same* `process_once` loop as `pcn run-all` and launchd. Lease-based claiming makes the web worker safe to run alongside launchd.
- Run history and live task state come from the existing `job_runs` and `tasks` tables — there is no separate run table.

## Acceptance gate

- `require_accepted_transcripts` defaults to `false`. With it off, autonomous launchd runs are unchanged.
- Set it `true` under `[llm]` to require that only `accepted` transcript segments feed session summaries and daily context (human-in-the-loop).

## What the v1 panel can and cannot accept

- **In the panel:** accept/reject/flag transcript segments; correct speaker→person attribution.
- **Read-only in the panel:** LLM session summary, daily context, and memory candidates.
- **Still in Obsidian:** final confirm/reject/edit of memory candidates (`confirm_checked_candidates`). A second HTTP mutation path would create a competing source of truth, so it is deferred.

## Boundaries

- Audio and raw transcripts stay local.
- Obsidian remains the final knowledge surface and the candidate control surface in v1.
- Speaker→person edits write the database through the same writer the markdown sync uses; the database is authoritative.
