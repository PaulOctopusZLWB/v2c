export function RunInspector({
  workerRunning,
  taskCount,
  onRun,
  onStop
}: {
  workerRunning: boolean;
  taskCount: number;
  onRun: () => void;
  onStop: () => void;
}) {
  return (
    <aside className="run-inspector">
      <h2>Run Inspector</h2>
      <p>{workerRunning ? "Running" : "Idle"}</p>
      <p>{taskCount} tasks</p>
      <button onClick={onRun} disabled={workerRunning}>Run</button>
      <button onClick={onStop} disabled={!workerRunning}>Stop</button>
    </aside>
  );
}
