import { useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
import { RunInspector } from "./components/RunInspector";
import { TaskList } from "./components/TaskList";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { activeStage } from "./lib/stages";

export function App() {
  const { tasks, worker_running } = usePipelineStatus();
  const [sourceDir, setSourceDir] = useState("");

  async function handleImport() {
    if (!sourceDir) return;
    await api.importDir(sourceDir);
    await api.run(); // default import enqueues only; explicitly start the background worker
  }

  return (
    <main className="workbench">
      <aside className="pipeline-rail">
        <PipelineRail activeStage={activeStage(tasks)} />
      </aside>
      <section className="main-panel">
        <h1>Personal Context Node</h1>
        <label>
          Source directory
          <input value={sourceDir} onChange={(event) => setSourceDir(event.target.value)} placeholder="/path/to/recordings" />
        </label>
        <button onClick={handleImport}>Import</button>
        <h2>Tasks</h2>
        <TaskList tasks={tasks} onRetry={(taskId) => api.retry(taskId)} />
      </section>
      <RunInspector
        workerRunning={worker_running}
        taskCount={tasks.length}
        onRun={() => api.run()}
        onStop={() => api.stop()}
      />
    </main>
  );
}
