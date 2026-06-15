import { useEffect, useState } from "react";
import { subscribeStatus } from "../api/events";
import type { ImportProgress, StatusSummary } from "../api/types";

export interface PipelineStatus {
  /** Compact live aggregate from the SSE `status.summary` event; null until the first frame. */
  summary: StatusSummary | null;
  worker_running: boolean;
  import_progress?: ImportProgress | null;
}

/**
 * Subscribe to the compact live pipeline summary. The full task list is no longer
 * streamed every tick — components that need it (TaskList) fetch it lazily via
 * `api.statusTasks()` when their panel opens.
 */
export function usePipelineStatus(): PipelineStatus {
  const [summary, setSummary] = useState<StatusSummary | null>(null);
  useEffect(() => {
    let active = true;
    const unsubscribe = subscribeStatus((s) => active && setSummary(s));
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return {
    summary,
    worker_running: summary?.worker_running ?? false,
    import_progress: summary?.import_progress ?? null
  };
}
