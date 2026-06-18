import type { WorkflowStep } from "../../components/ui";

export type ProjectionWorkflowState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "ready"; pointCount: number; capped: boolean; total?: number };

export interface VoiceprintWorkflowInput {
  selectedScopeCount: number;
  projection: ProjectionWorkflowState;
  selectedSegmentCount: number;
  hasKnownPeople: boolean;
  lastAutoAttributeCount: number | null;
  hasReviewTarget: boolean;
}

export function buildVoiceprintWorkflow(input: VoiceprintWorkflowInput): WorkflowStep[] {
  const hasScope = input.selectedScopeCount > 0;
  const projected = input.projection.status === "ready";
  const hasSelection = input.selectedSegmentCount > 0;
  const identified = input.lastAutoAttributeCount !== null;

  return [
    {
      id: "scope",
      label: "选择范围",
      state: hasScope ? "complete" : "current",
      detail: hasScope ? `${input.selectedScopeCount} 个范围` : "选择日期或会话"
    },
    {
      id: "project",
      label: "投射",
      state: projectStepState(input.projection, hasScope),
      detail: projectStepDetail(input.projection, hasScope)
    },
    {
      id: "label",
      label: "框选/标注",
      state: !projected ? "pending" : hasSelection ? "complete" : "current",
      detail: hasSelection ? `已选 ${input.selectedSegmentCount} 段` : "在图上框选样本"
    },
    {
      id: "identify",
      label: "全局识别",
      state: !projected ? "pending" : identified ? "complete" : hasSelection || input.hasKnownPeople ? "current" : "blocked",
      detail: identified ? `已归属 ${input.lastAutoAttributeCount} 段` : input.hasKnownPeople ? "按已登记声纹归属" : "先登记至少一人"
    },
    {
      id: "verify",
      label: "回审核验证",
      state: identified ? "current" : "pending",
      detail: input.hasReviewTarget ? "打开待审会话核对" : "识别后回到审核"
    }
  ];
}

function projectStepState(projection: ProjectionWorkflowState, hasScope: boolean): WorkflowStep["state"] {
  if (!hasScope) return "blocked";
  if (projection.status === "loading") return "running";
  if (projection.status === "ready") return "complete";
  if (projection.status === "error" || projection.status === "empty") return "blocked";
  return "current";
}

function projectStepDetail(projection: ProjectionWorkflowState, hasScope: boolean): string {
  if (!hasScope) return "先选择范围";
  if (projection.status === "loading") return "正在计算声纹云图";
  if (projection.status === "empty") return "范围内没有声纹";
  if (projection.status === "error") return projection.message;
  if (projection.status === "ready") return `${projection.pointCount} 点${projection.capped ? " · 已采样" : ""}`;
  return "点击投射生成地图";
}
