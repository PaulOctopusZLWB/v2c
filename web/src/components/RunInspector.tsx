import { t } from "../i18n";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { Icon } from "./Icon";

export function RunInspector({
  workerRunning, taskCount, gateOn, onRun, onStop
}: {
  workerRunning: boolean; taskCount: number; gateOn?: boolean; onRun: () => Promise<unknown> | void; onStop: () => Promise<unknown> | void;
}) {
  const run = useAsyncAction(async () => { await onRun(); });
  const stop = useAsyncAction(async () => { await onStop(); });
  return (
    <section className="run-inspector card">
      <div className="section-title">
        {workerRunning ? <span className="live-dot" aria-hidden /> : null}
        {workerRunning ? t.run.running : t.run.idle}
      </div>
      <p className="muted">
        <span className="num">{taskCount}</span> {t.run.tasks}
      </p>
      <p>
        <span className={`badge${gateOn ? " s-accepted" : ""}`}>{gateOn ? t.gate.on : t.gate.off}</span>
      </p>
      <div className="run-actions">
        <button
          className="primary"
          onClick={() => void run.run()}
          disabled={workerRunning || run.pending}
          aria-busy={run.pending}
        >
          {run.pending ? <span className="spinner" aria-hidden /> : <Icon name="run" />}
          {run.pending ? "正在启动…" : t.run.run}
        </button>
        <button
          onClick={() => void stop.run()}
          disabled={!workerRunning || stop.pending}
          aria-busy={stop.pending}
        >
          {stop.pending ? <span className="spinner" aria-hidden /> : <Icon name="stop" />}
          {stop.pending ? "正在停止…" : t.run.stop}
        </button>
      </div>
    </section>
  );
}
