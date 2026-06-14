import type { TaskRow } from "../api/types";

export type Stage = "device" | "import" | "asr" | "review" | "llm" | "publish";

export const STAGES: Array<{ id: Stage; label: string }> = [
  { id: "device", label: "设备" },
  { id: "import", label: "导入" },
  { id: "asr", label: "转写" },
  { id: "review", label: "审核" },
  { id: "llm", label: "观点" },
  { id: "publish", label: "发布" }
];

export function stageForTaskType(taskType: string): Stage {
  if (taskType === "vad" || taskType === "asr") return "asr";
  if (taskType === "session_derive" || taskType === "summarize_session" || taskType === "daily_generate") return "llm";
  if (taskType === "obsidian_publish" || taskType === "archive") return "publish";
  return "import";
}

export function activeStage(tasks: TaskRow[]): Stage {
  const live = tasks.find((t) => t.status === "running") ?? tasks.find((t) => t.status === "pending");
  return live ? stageForTaskType(live.task_type) : "device";
}
