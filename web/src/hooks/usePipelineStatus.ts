import { useEffect, useState } from "react";
import { subscribeStatus } from "../api/events";
import { api } from "../api/client";
import type { StatusSnapshot } from "../api/types";

export function usePipelineStatus(): StatusSnapshot {
  const [snapshot, setSnapshot] = useState<StatusSnapshot>({ tasks: [], worker_running: false });
  useEffect(() => {
    let active = true;
    // Seed from a one-shot GET so the UI is populated before the first SSE frame.
    api.statusTasks().then((r) => active && setSnapshot((s) => ({ ...s, tasks: r.tasks }))).catch(() => undefined);
    const unsubscribe = subscribeStatus((snap) => active && setSnapshot(snap));
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return snapshot;
}
