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
  etaSeconds,
  etaConfidence
}: {
  done: number;
  total: number;
  label?: string;
  stages?: StageCount[];
  etaSeconds?: number | null;
  etaConfidence?: "live" | "historical" | "partial" | "learning" | "none";
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
        {etaSeconds != null && etaConfidence && etaConfidence !== "none" ? (
          <span className="progress-eta-basis">
            {etaConfidence === "live" ? "实时估算" : etaConfidence === "historical" ? "按历史耗时" : etaConfidence === "partial" ? "粗略估算" : "正在学习"}
          </span>
        ) : null}
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
        <div className="progress-stages">
          {stages.map((s) => {
            const r = s.total > 0 ? Math.max(0, Math.min(1, s.done / s.total)) : 0;
            const p = Math.round(r * 100);
            return (
              <div key={s.label} className="progress-stage" title={`${s.label} ${s.done}/${s.total} · ${p}%`}>
                <span className="progress-stage-label">{s.label}</span>
                <div className="progress-stage-track" aria-hidden>
                  <div className="progress-stage-bar" style={{ width: `${p}%` }} />
                </div>
                <span className="num progress-stage-count">{s.done}/{s.total}</span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
