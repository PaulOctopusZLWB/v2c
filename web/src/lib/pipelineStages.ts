import type { ImportProgress, StatusSummary } from "../api/types";

/* 管道六阶段(导入/VAD/转写/切分/摘要/发布)的共享推导:今日横条(spine)与
 * 管道页阶段栈都从 status.summary 聚合出同一组状态。 */

export type PipelineStageState = "done" | "running" | "pending";

export interface PipelineStage {
  label: string;
  state: PipelineStageState;
  pct?: number;
  /** 组内任务完成数/总数(导入阶段来自 import_progress 的文件数)。 */
  done: number;
  total: number;
}

function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

const GROUPS: Array<{ label: string; types: string[] }> = [
  { label: "VAD", types: ["vad"] },
  { label: "转写", types: ["asr"] },
  { label: "切分", types: ["session_derive"] },
  { label: "摘要", types: ["summarize_session", "daily_generate"] },
  { label: "发布", types: ["obsidian_publish", "archive"] }
];

/** 组内 done==total → done;活动阶段或部分完成 → running(带百分比);其余 pending。 */
export function pipelineStages(
  summary: StatusSummary | null,
  importProgress?: ImportProgress | null
): PipelineStage[] {
  const counts = summary?.stage_counts ?? {};
  const active = summary?.active_stage ?? null;

  const importStage: PipelineStage = importProgress?.active
    ? {
        label: "导入",
        state: "running",
        pct: pct(importProgress.done, importProgress.total),
        done: importProgress.done,
        total: importProgress.total
      }
    : {
        label: "导入",
        state: summary && (summary.total ?? 0) > 0 ? "done" : "pending",
        done: importProgress?.done ?? 0,
        total: importProgress?.total ?? 0
      };

  return [
    importStage,
    ...GROUPS.map(({ label, types }) => {
      let done = 0;
      let total = 0;
      for (const t of types) {
        done += counts[t]?.done ?? 0;
        total += counts[t]?.total ?? 0;
      }
      if (total > 0 && done >= total) return { label, state: "done" as const, done, total };
      if ((active && types.includes(active)) || (total > 0 && done > 0))
        return { label, state: "running" as const, pct: pct(done, total), done, total };
      return { label, state: "pending" as const, done, total };
    })
  ];
}
