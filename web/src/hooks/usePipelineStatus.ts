import { useEffect, useRef, useState } from "react";
import { subscribePipeline } from "../api/events";
import type { ImportProgress, RunCompleted, StatusSummary } from "../api/types";

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
 *
 * `onRunCompleted`(可选)在一次运行收尾时触发(App 用它弹「立即审核 ↵」toast /
 * 自动跳转);经 ref 转发,调用方可以传内联闭包而不引发重订阅。
 */
export function usePipelineStatus(handlers?: { onRunCompleted?: (e: RunCompleted) => void }): PipelineStatus {
  const [summary, setSummary] = useState<StatusSummary | null>(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  useEffect(() => {
    let active = true;
    const unsubscribe = subscribePipeline({
      "status.summary": (s) => active && setSummary(s),
      "run.completed": (e) => active && handlersRef.current?.onRunCompleted?.(e)
    });
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
