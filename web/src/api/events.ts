import type { StatusSummary } from "./types";

/**
 * Subscribe to the live pipeline status. The backend now pushes a compact
 * `status.summary` event (counts/total/active_stage/...) every tick instead of
 * the full ~1881-row task array — the full list is fetched lazily on demand via
 * `GET /api/status/tasks` (see `api.statusTasks`).
 */
export function subscribeStatus(onSummary: (summary: StatusSummary) => void): () => void {
  const source = new EventSource("/api/events");
  source.addEventListener("status.summary", (event) =>
    onSummary(JSON.parse((event as MessageEvent).data) as StatusSummary)
  );
  return () => source.close();
}
