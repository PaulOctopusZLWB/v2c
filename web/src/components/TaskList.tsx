import type { TaskRow } from "../api/types";
import { t } from "../i18n";
import { useAsyncAction } from "../hooks/useAsyncAction";

const STATUS_LABELS: Record<string, string> = {
  pending: "待认领",
  claimed: "处理中",
  running: "运行中",
  done: "完成",
  failed: "失败"
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function TaskRowView({ task, onRetry }: { task: TaskRow; onRetry: (taskId: string) => Promise<unknown> | void }) {
  const failed = task.status.startsWith("failed");
  const retry = useAsyncAction(async (taskId: string) => { await onRetry(taskId); });
  return (
    <div className="task-row">
      <span>{task.task_type}</span>
      <span className={failed ? "status-failed" : undefined}>{statusLabel(task.status)}</span>
      {failed ? (
        <button onClick={() => void retry.run(task.task_id)} disabled={retry.pending} aria-busy={retry.pending}>
          {retry.pending ? "正在重试…" : t.run.retry}
        </button>
      ) : null}
    </div>
  );
}

export function TaskList({ tasks, onRetry }: { tasks: TaskRow[]; onRetry: (taskId: string) => Promise<unknown> | void }) {
  return (
    <div className="task-list">
      {tasks.map((task) => (
        <TaskRowView key={task.task_id} task={task} onRetry={onRetry} />
      ))}
    </div>
  );
}
