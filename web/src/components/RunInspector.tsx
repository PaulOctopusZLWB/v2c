import { t } from "../i18n";
import { useAsyncAction } from "../hooks/useAsyncAction";

export function RunInspector({
  workerRunning, taskCount, gateOn, onRun, onStop
}: {
  workerRunning: boolean; taskCount: number; gateOn?: boolean; onRun: () => Promise<unknown> | void; onStop: () => Promise<unknown> | void;
}) {
  const run = useAsyncAction(async () => { await onRun(); });
  const stop = useAsyncAction(async () => { await onStop(); });
  return (
    <aside className="run-inspector">
      <h2>{workerRunning ? <><span className="live-dot" /> {t.run.running}</> : t.run.idle}</h2>
      <p className="num">{taskCount} {t.run.tasks}</p>
      <p className="dim">{gateOn ? t.gate.on : t.gate.off}</p>
      <button onClick={() => void run.run()} disabled={workerRunning || run.pending} aria-busy={run.pending}>
        {run.pending ? "正在启动…" : t.run.run}
      </button>
      <button onClick={() => void stop.run()} disabled={!workerRunning || stop.pending} aria-busy={stop.pending}>
        {stop.pending ? "正在停止…" : t.run.stop}
      </button>
    </aside>
  );
}
