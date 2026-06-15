import { useMemo, useState } from "react";
import type { TaskRow } from "../api/types";
import { t } from "../i18n";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { taskStatusZh, taskTypeZh } from "../lib/format";
import { Icon } from "./Icon";

// Manual virtualization constants — no new dependency. We render a bounded window of
// rows around the current scroll offset plus a small overscan, with spacer divs above
// and below to preserve the scrollbar geometry of the full list.
const ROW_HEIGHT = 88; // px; fixed so virtualization geometry is exact — sized to fit a
// failed row (type+badge+retry, meta, and a 2-line clamped error) so content never overlaps.
const VIEWPORT_ROWS = 12; // visible rows in the scroll area
const OVERSCAN = 6; // extra rows above/below the viewport
const WINDOW = VIEWPORT_ROWS + OVERSCAN * 2; // total rows rendered at once (<= 60 DOM rows)

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
    <div className="task-row row-btn" aria-disabled style={{ height: ROW_HEIGHT }}>
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
      {task.last_error ? <span className="task-error" title={task.last_error}>{task.last_error}</span> : null}
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
  const [failedOnly, setFailedOnly] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
  const retryAll = useAsyncAction(async () => { await onRetryAllFailed?.(); });

  const visible = useMemo(
    () => (failedOnly ? tasks.filter((tk) => tk.status.startsWith("failed")) : tasks),
    [tasks, failedOnly]
  );

  // The summary feeds a count even before the (lazy) full list loads, so the panel can
  // open at scale without holding the ~1881-row array.
  const count = taskCount ?? tasks.length;
  // Failed count: prefer the summary-derived value, else compute from the loaded rows.
  const failed = failedCount ?? tasks.filter((tk) => tk.status.startsWith("failed")).length;
  if (count === 0) return null;

  // Windowed slice: which rows fall in the rendered window given the scroll offset.
  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const start = Math.min(first, Math.max(0, visible.length - WINDOW));
  const end = Math.min(visible.length, start + WINDOW);
  const windowRows = visible.slice(start, end);
  const padTop = start * ROW_HEIGHT;
  const padBottom = Math.max(0, (visible.length - end) * ROW_HEIGHT);

  return (
    <details className="task-list" onToggle={(e) => onToggle?.((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="section-title">
        <Icon name="run" /> {t.nav.tasks} ({count})
      </summary>
      <div className="task-list-controls">
        <label className="task-filter">
          <input type="checkbox" checked={failedOnly} onChange={(e) => setFailedOnly(e.target.checked)} />
          {t.run.failedOnly}
        </label>
        {failed > 0 && onRetryAllFailed ? (
          <button
            className="ghost"
            onClick={() => void retryAll.run()}
            disabled={retryAll.pending}
            aria-busy={retryAll.pending}
          >
            {retryAll.pending ? <span className="spinner" aria-hidden /> : <Icon name="refresh" />}
            {t.run.retryAllFailed} ({failed})
          </button>
        ) : null}
      </div>
      <div
        className="task-rows"
        style={{ maxHeight: VIEWPORT_ROWS * ROW_HEIGHT, overflowY: "auto" }}
        onScroll={(e) => setScrollTop((e.currentTarget as HTMLDivElement).scrollTop)}
      >
        <div style={{ height: padTop }} aria-hidden />
        {windowRows.map((task) => (
          <TaskRowView key={task.task_id} task={task} onRetry={onRetry} />
        ))}
        <div style={{ height: padBottom }} aria-hidden />
      </div>
    </details>
  );
}
