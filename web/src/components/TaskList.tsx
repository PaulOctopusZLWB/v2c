import type { TaskRow } from "../api/types";
import { t } from "../i18n";

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

export function TaskList({ tasks, onRetry }: { tasks: TaskRow[]; onRetry: (taskId: string) => void }) {
  return (
    <div className="task-list">
      {tasks.map((task) => {
        const failed = task.status.startsWith("failed");
        return (
          <div className="task-row" key={task.task_id}>
            <span>{task.task_type}</span>
            <span className={failed ? "status-failed" : undefined}>{statusLabel(task.status)}</span>
            {failed ? <button onClick={() => onRetry(task.task_id)}>{t.run.retry}</button> : null}
          </div>
        );
      })}
    </div>
  );
}
