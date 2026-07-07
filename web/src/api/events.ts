import type { LiveSegment, RunCompleted, StageChanged, StatusSummary, TaskFailed, TaskProgress } from "./types";

/* /api/events SSE:后端 1s 轮询推导出的事件流。流在空闲时自行关闭,浏览器
 * EventSource 会自动重连(即断线降级为「隔几秒重询」)。 */

export interface PipelineEventHandlers {
  "status.summary"?: (summary: StatusSummary) => void;
  "segment.transcribed"?: (segment: LiveSegment) => void;
  "stage.changed"?: (event: StageChanged) => void;
  "task.failed"?: (event: TaskFailed) => void;
  "task.progress"?: (event: TaskProgress) => void;
  "run.completed"?: (event: RunCompleted) => void;
}

/** Subscribe to any subset of the pipeline event stream; returns an unsubscribe. */
export function subscribePipeline(handlers: PipelineEventHandlers): () => void {
  const source = new EventSource("/api/events");
  for (const [name, handler] of Object.entries(handlers)) {
    if (!handler) continue;
    source.addEventListener(name, (event) =>
      (handler as (payload: unknown) => void)(JSON.parse((event as MessageEvent).data))
    );
  }
  return () => source.close();
}

/**
 * Subscribe to the live pipeline status. The backend pushes a compact
 * `status.summary` event (counts/total/active_stage/...) every tick; the full
 * task list is fetched lazily on demand via `GET /api/status/tasks`.
 */
export function subscribeStatus(onSummary: (summary: StatusSummary) => void): () => void {
  return subscribePipeline({ "status.summary": onSummary });
}
