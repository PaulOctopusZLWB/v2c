/**
 * Labeled progress bar driven by live task counts. Renders nothing when there
 * is nothing to track (total === 0).
 *
 * Optional `stages` renders a per-stage count breakdown (e.g. `转写 1200/1500`),
 * and `etaSeconds` renders a coarse remaining-time estimate.
 */
export interface StageCount {
  label: string;
  done: number;
  total: number;
}

function etaLabel(seconds: number): string {
  if (seconds <= 0) return "剩余约 不到 1 分钟";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `剩余约 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem ? `剩余约 ${hours} 小时 ${rem} 分钟` : `剩余约 ${hours} 小时`;
}

export function Progress({
  done,
  total,
  label,
  stages,
  etaSeconds
}: {
  done: number;
  total: number;
  label?: string;
  stages?: StageCount[];
  etaSeconds?: number | null;
}) {
  if (total === 0) return null;
  const ratio = Math.max(0, Math.min(1, done / total));
  const pct = Math.round(ratio * 100);
  return (
    <div className="progress">
      <span className="progress-label">
        处理中 <span className="num">{done}/{total}</span>
        {label ? <> · {label}</> : null}
        <span className="num progress-pct"> {pct}%</span>
        {etaSeconds != null ? <span className="progress-eta"> · {etaLabel(etaSeconds)}</span> : null}
      </span>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={label}
      >
        <div className="progress-bar" style={{ width: `${pct}%` }} />
      </div>
      {stages && stages.length > 0 ? (
        <span className="progress-stages">
          {stages.map((s) => (
            <span key={s.label} className="progress-stage">
              {s.label} <span className="num">{s.done}/{s.total}</span>
            </span>
          ))}
        </span>
      ) : null}
    </div>
  );
}
