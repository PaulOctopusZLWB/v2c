import type { TaskRow } from "../api/types";

export function TaskList({ tasks, onRetry }: { tasks: TaskRow[]; onRetry: (taskId: string) => void }) {
  return (
    <div className="task-list">
      {tasks.map((task) => (
        <div className="task-row" key={task.task_id}>
          <span>{task.task_type}</span>
          <span>{task.status}</span>
          {task.status.startsWith("failed") ? (
            <button onClick={() => onRetry(task.task_id)}>Retry</button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
