import { t } from "../i18n";

export function RunInspector({
  workerRunning, taskCount, gateOn, onRun, onStop
}: {
  workerRunning: boolean; taskCount: number; gateOn?: boolean; onRun: () => void; onStop: () => void;
}) {
  return (
    <aside className="run-inspector">
      <h2>{workerRunning ? <><span className="live-dot" /> {t.run.running}</> : t.run.idle}</h2>
      <p className="num">{taskCount} {t.run.tasks}</p>
      <p className="dim">{gateOn ? t.gate.on : t.gate.off}</p>
      <button onClick={onRun} disabled={workerRunning}>{t.run.run}</button>
      <button onClick={onStop} disabled={!workerRunning}>{t.run.stop}</button>
    </aside>
  );
}
