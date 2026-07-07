import { useEffect, useRef, useState } from "react";
import { subscribePipeline } from "../api/events";
import type { LiveSegment, RunCompleted } from "../api/types";
import { taskTypeZh } from "../lib/format";

/* 管道控制室的实时数据(design handoff Phase 4):
 *  - segments: 实时转写流缓冲(最近 MAX_SEGMENTS 条,新的在尾部)。
 *  - tail:    mono 事件流(阶段切换/失败/完成),最近 MAX_TAIL 条。
 *  - completed: 最近一次 run.completed(新活动开始后清空)。
 * 只在管道页挂载时开一条 EventSource;卸载即断开。 */

const MAX_SEGMENTS = 60;
const MAX_TAIL = 30;

export interface FeedTailEntry {
  id: number;
  kind: "stage" | "failed" | "completed";
  label: string;
}

export function usePipelineFeed(): {
  segments: LiveSegment[];
  tail: FeedTailEntry[];
  completed: RunCompleted | null;
} {
  const [segments, setSegments] = useState<LiveSegment[]>([]);
  const [tail, setTail] = useState<FeedTailEntry[]>([]);
  const [completed, setCompleted] = useState<RunCompleted | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    const pushTail = (kind: FeedTailEntry["kind"], label: string) =>
      setTail((prev) => [...prev.slice(-(MAX_TAIL - 1)), { id: ++seq.current, kind, label }]);

    return subscribePipeline({
      "segment.transcribed": (seg) => {
        setCompleted(null); // 新段进来 = 新活动,清掉上一次完成态
        setSegments((prev) => [...prev.slice(-(MAX_SEGMENTS - 1)), seg]);
      },
      "stage.changed": (e) =>
        pushTail("stage", `→ ${taskTypeZh(e.stage)}${e.target ? ` · ${e.target}` : ""}`),
      "task.failed": (e) =>
        pushTail("failed", `✕ ${taskTypeZh(e.task_type)} 失败 · ${e.target_id}${e.error ? ` · ${e.error}` : ""}`),
      "run.completed": (e) => {
        setCompleted(e);
        pushTail("completed", `✓ 运行完成 · ${e.done_total}/${e.total}${e.failed_total ? ` · 失败 ${e.failed_total}` : ""}`);
      }
    });
  }, []);

  return { segments, tail, completed };
}
