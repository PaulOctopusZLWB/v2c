import type { TaskRow } from "../api/types";
import { t } from "../i18n";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { taskStatusZh, taskTypeZh } from "../lib/format";
import { Icon } from "./Icon";

function badgeClassFor(status: string): string {
  if (status === "succeeded") return "badge s-accepted";
  if (status.startsWith("failed")) return "badge s-rejected";
  if (status === "running" || status === "claimed") return "badge s-needs_fix";
  return "badge s-pending_review";
}

function TaskRowView({ task, onRetry }: { task: TaskRow; onRetry: (taskId: string) => Promise<unknown> | void }) {
  const failed = task.status.startsWith("failed");
  const retry = useAsyncAction(async (taskId: string) => { await onRetry(taskId); });
  return (
    <div className="task-row row-btn" aria-disabled>
      <span>{taskTypeZh(task.task_type)}</span>
      <span className="task-row-end">
        <span className={badgeClassFor(task.status)}>{taskStatusZh(task.status)}</span>
        {failed ? (
          <button
            className="ghost"
            onClick={() => void retry.run(task.task_id)}
            disabled={retry.pending}
            aria-busy={retry.pending}
          >
            {retry.pending ? <span className="spinner" aria-hidden /> : <Icon name="refresh" />}
            {retry.pending ? "正在重试…" : t.run.retry}
          </button>
        ) : null}
      </span>
    </div>
  );
}

export function TaskList({ tasks, onRetry }: { tasks: TaskRow[]; onRetry: (taskId: string) => Promise<unknown> | void }) {
  if (tasks.length === 0) return null;
  return (
    <details className="task-list">
      <summary className="section-title">
        <Icon name="run" /> {t.nav.tasks} ({tasks.length})
      </summary>
      <div className="task-rows">
        {tasks.map((task) => (
          <TaskRowView key={task.task_id} task={task} onRetry={onRetry} />
        ))}
      </div>
    </details>
  );
}
