/**
 * Labeled progress bar driven by live task counts. Renders nothing when there
 * is nothing to track (total === 0).
 */
export function Progress({ done, total, label }: { done: number; total: number; label?: string }) {
  if (total === 0) return null;
  const ratio = Math.max(0, Math.min(1, done / total));
  const pct = Math.round(ratio * 100);
  return (
    <div className="progress">
      <span className="progress-label">
        处理中 <span className="num">{done}/{total}</span>
        {label ? <> · {label}</> : null}
        <span className="num progress-pct"> {pct}%</span>
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
    </div>
  );
}
