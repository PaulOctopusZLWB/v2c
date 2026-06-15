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
      <span className="task-meta num">
        <span>{task.target_id}</span> · attempt <span>{task.attempt_count}</span>
      </span>
      {task.last_error ? <span className="task-error">{task.last_error}</span> : null}
    </div>
  );
}

export function TaskList({
  tasks,
  taskCount,
  failedCount,
  onToggle,
  onRetry,
  onRetryAllFailed
}: {
  tasks: TaskRow[];
  taskCount?: number;
  failedCount?: number;
  onToggle?: (open: boolean) => void;
  onRetry: (taskId: string) => Promise<unknown> | void;
  onRetryAllFailed?: () => Promise<unknown> | void;
}) {
  // The summary feeds a count even before the (lazy) full list loads, so the panel can
  // open at scale without holding the ~1881-row array.
  const count = taskCount ?? tasks.length;
  if (count === 0) return null;
  return (
    <details className="task-list" onToggle={(e) => onToggle?.((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="section-title">
        <Icon name="run" /> {t.nav.tasks} ({count})
      </summary>
      <div className="task-rows">
        {tasks.map((task) => (
          <TaskRowView key={task.task_id} task={task} onRetry={onRetry} />
        ))}
      </div>
    </details>
  );
}
