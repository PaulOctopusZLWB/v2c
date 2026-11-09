import type { ProjectionRequest, ProjectionScopeStatus } from "../../api/types";
import { Icon } from "../../components/Icon";

export type ProjParams = Pick<ProjectionRequest, "method" | "n_neighbors" | "min_dist">;
export const PROJ_DEFAULTS: Required<ProjParams> = { method: "umap", n_neighbors: 15, min_dist: 0.1 };

export function ProjectionControls({ value, onChange, onApply, scopeStatus, counting, scopeError, processing }: {
  value: ProjParams;
  onChange: (value: ProjParams) => void;
  onApply: () => void;
  scopeStatus?: ProjectionScopeStatus | null;
  counting?: boolean;
  scopeError?: string | null;
  processing?: boolean;
}) {
  const n = scopeStatus?.embedded ?? 0;
  const limit = scopeStatus?.limit ?? 16000;
  const over = n > limit;
  return (
    <section className="projection-controls card">
      <div className="section-title" style={{ margin: 0 }}><Icon name="viewpoint" /> UMAP 投射</div>
      <div className="projection-scope-count" role="status" aria-live="polite">
        {counting ? "正在计算范围…" : scopeError ? <span role="alert">范围读取失败：{scopeError}</span> : (
          <>
            <div>已选可投射 <strong className="num">{n.toLocaleString()} / {limit.toLocaleString()}</strong></div>
            {scopeStatus && scopeStatus.pending > 0 ? <div className="muted">另有 {scopeStatus.pending.toLocaleString()} 段待提取</div> : null}
            {over ? <div className="projection-scope-over">超过上限 {(n - limit).toLocaleString()} 段，请缩小范围。</div> : null}
            {n > 0 && n < 5 ? <div className="muted">UMAP 至少需要 5 段已提取声纹。</div> : null}
          </>
        )}
      </div>
      <button type="button" className="primary proj-apply" onClick={onApply}
        disabled={!!counting || !!scopeError || !!processing || n < 5 || over}>
        <Icon name="viewpoint" /> {processing ? "正在生成…" : "生成云图"}
      </button>
      <details className="projection-advanced">
        <summary>UMAP 参数</summary>
        <div className="proj-params">
          <label className="proj-slider"><span className="proj-slider-head"><span>n_neighbors</span><span className="num">{value.n_neighbors ?? 15}</span></span>
            <input type="range" aria-label="n_neighbors" min={2} max={50} step={1} value={value.n_neighbors ?? 15}
              onChange={(e) => onChange({ ...value, n_neighbors: Number(e.target.value) })} />
          </label>
          <label className="proj-slider"><span className="proj-slider-head"><span>min_dist</span><span className="num">{(value.min_dist ?? 0.1).toFixed(2)}</span></span>
            <input type="range" aria-label="min_dist" min={0} max={0.99} step={0.05} value={value.min_dist ?? 0.1}
              onChange={(e) => onChange({ ...value, min_dist: Number(e.target.value) })} />
          </label>
        </div>
      </details>
    </section>
  );
}
