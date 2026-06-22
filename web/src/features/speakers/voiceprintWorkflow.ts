import type { WorkflowStep } from "../../components/ui";

/** Identification progress over all active segments (GET /api/speakers/identification-status). */
export interface IdentificationStatus {
  total: number;
  embedded: number;
  clusters: number;
  identified: number;
  unidentified: number;
}

export interface VoiceprintWorkflowInput {
  status: IdentificationStatus | null;
}

/**
 * The identify-first pipeline that gates summaries: 提取声纹 → 自动聚类 → 分配聚类 → 清理噪音 → 确认.
 * Every step's state is derived from real counts; the run is done when 未识别 (unidentified) hits 0.
 */
export function buildVoiceprintWorkflow(input: VoiceprintWorkflowInput): WorkflowStep[] {
  const s = input.status;
  const total = s?.total ?? 0;
  const embedded = s?.embedded ?? 0;
  const clusters = s?.clusters ?? 0;
  const identified = s?.identified ?? 0;
  const unidentified = s?.unidentified ?? 0;
  const hasData = total > 0;
  const allEmbedded = hasData && embedded >= total;
  const done = hasData && unidentified === 0;

  return [
    {
      id: "extract",
      label: "提取声纹",
      state: !hasData ? "current" : allEmbedded ? "complete" : embedded > 0 ? "running" : "current",
      detail: hasData ? `已抽 ${embedded}/${total}` : "导入并转写后在工具栏提取",
    },
    {
      id: "cluster",
      label: "自动聚类",
      state: embedded === 0 ? "pending" : clusters > 0 ? "complete" : "current",
      detail: clusters > 0 ? `${clusters} 个声纹组` : "把声纹分成 vp_ 组",
    },
    {
      id: "assign",
      label: "分配聚类",
      state: clusters === 0 ? "pending" : done ? "complete" : "current",
      detail: hasData ? `已识别 ${identified}/${total}` : "逐组选人,整组归属",
    },
    {
      id: "noise",
      label: "清理噪音",
      state: clusters === 0 ? "pending" : done ? "complete" : "current",
      detail: "语气词/短段一键归噪音",
    },
    {
      id: "confirm",
      label: "确认",
      state: !hasData ? "pending" : done ? "complete" : "current",
      detail: done ? "可进入汇总" : `未识别 ${unidentified} 段`,
    },
  ];
}
