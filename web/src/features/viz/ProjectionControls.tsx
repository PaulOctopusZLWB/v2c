import type { ProjectionMethod, ProjectionRequest } from "../../api/types";
import { Icon } from "../../components/Icon";
import { Select } from "../../components/ui/Select";

/** The tunable subset of a projection request this control owns. */
export type ProjParams = Pick<
  ProjectionRequest,
  "method" | "n_neighbors" | "min_dist" | "pca_x" | "pca_y" | "perplexity" | "max_points"
>;

/** Sensible defaults for each method's params (backend has matching defaults). */
export const PROJ_DEFAULTS: Required<Omit<ProjParams, "method">> & { method: ProjectionMethod } = {
  method: "umap",
  n_neighbors: 15,
  min_dist: 0.1,
  pca_x: 0,
  pca_y: 1,
  perplexity: 30,
  max_points: 4000
};

const METHODS: Array<{ id: ProjectionMethod; label: string; title?: string }> = [
  { id: "umap", label: "UMAP" },
  { id: "pca", label: "PCA", title: "快速预览" },
  { id: "tsne", label: "t-SNE", title: "较慢" }
];

/** PC1..PC8 dropdown options; value is the 0-based component index (pca_x/pca_y). */
const PCS = Array.from({ length: 8 }, (_, i) => i);

/**
 * 投射控制 — the tunable projection panel. A segmented UMAP / PCA / t-SNE toggle plus
 * method-specific params. PERF: param edits only update local state via onChange; they do NOT
 * refetch. Only the prominent 投射 button (onApply) — or a method change (which the parent may
 * auto-apply) — triggers a projection, so dragging a slider stays smooth.
 */
export function ProjectionControls({
  value,
  onChange,
  onApply,
  capped,
  n,
  total
}: {
  value: ProjParams;
  onChange: (v: ProjParams, apply?: boolean) => void;
  onApply: () => void;
  /** Last result was evenly subsampled to stay responsive. */
  capped?: boolean;
  /** Points actually projected (after any subsample). */
  n?: number;
  /** Total in-scope points before subsampling. */
  total?: number;
}) {
  const method = value.method ?? "umap";
  const set = (patch: Partial<ProjParams>) => onChange({ ...value, ...patch });

  const nNeighbors = value.n_neighbors ?? PROJ_DEFAULTS.n_neighbors;
  const minDist = value.min_dist ?? PROJ_DEFAULTS.min_dist;
  const pcaX = value.pca_x ?? PROJ_DEFAULTS.pca_x;
  const pcaY = value.pca_y ?? PROJ_DEFAULTS.pca_y;
  const perplexity = value.perplexity ?? PROJ_DEFAULTS.perplexity;

  return (
    <section className="projection-controls card">
      <div className="section-title" style={{ margin: 0 }}>
        <Icon name="viewpoint" /> 投射方式
      </div>

      <div className="proj-method" role="group" aria-label="投影方法">
        {METHODS.map((m) => (
          <button
            key={m.id}
            type="button"
            className={method === m.id ? "active" : ""}
            aria-pressed={method === m.id}
            title={m.title}
            onClick={() => set({ method: m.id })}
          >
            {m.label}
          </button>
        ))}
      </div>

      {method === "umap" ? (
        <div className="proj-params">
          <label className="proj-slider">
            <span className="proj-slider-head">
              <span>n_neighbors</span>
              <span className="num">{nNeighbors}</span>
            </span>
            <input
              type="range"
              aria-label="n_neighbors"
              min={2}
              max={50}
              step={1}
              value={nNeighbors}
              onChange={(e) => set({ n_neighbors: Number(e.target.value) })}
            />
          </label>
          <label className="proj-slider">
            <span className="proj-slider-head">
              <span>min_dist</span>
              <span className="num">{minDist.toFixed(2)}</span>
            </span>
            <input
              type="range"
              aria-label="min_dist"
              min={0}
              max={0.99}
              step={0.05}
              value={minDist}
              onChange={(e) => set({ min_dist: Number(e.target.value) })}
            />
          </label>
        </div>
      ) : null}

      {method === "pca" ? (
        <div className="proj-params proj-pca">
          <div className="proj-dropdown">
            <span>主成分 X</span>
            <Select
              ariaLabel="主成分 X"
              value={String(pcaX)}
              options={PCS.map((i) => ({ value: String(i), label: `PC${i + 1}` }))}
              onChange={(v) => set({ pca_x: Number(v) })}
            />
          </div>
          <div className="proj-dropdown">
            <span>主成分 Y</span>
            <Select
              ariaLabel="主成分 Y"
              value={String(pcaY)}
              options={PCS.map((i) => ({ value: String(i), label: `PC${i + 1}` }))}
              onChange={(v) => set({ pca_y: Number(v) })}
            />
          </div>
        </div>
      ) : null}

      {method === "tsne" ? (
        <div className="proj-params">
          <label className="proj-slider">
            <span className="proj-slider-head">
              <span>perplexity</span>
              <span className="num">{perplexity}</span>
            </span>
            <input
              type="range"
              aria-label="perplexity"
              min={5}
              max={50}
              step={1}
              value={perplexity}
              onChange={(e) => set({ perplexity: Number(e.target.value) })}
            />
          </label>
          <p className="proj-hint muted">t-SNE 较慢,投射可能需要数秒。</p>
        </div>
      ) : null}

      <button type="button" className="primary proj-apply" onClick={onApply}>
        <Icon name="viewpoint" /> 投射
      </button>

      {capped ? (
        <div className="proj-capped" role="note">
          <p className="muted" style={{ margin: 0 }}>
            点数过多,已采样 <span className="num">{n ?? "?"}</span>/
            <span className="num">{total ?? "?"}</span>
          </p>
          <button
            type="button"
            className="proj-all"
            title="不采样,投射全部片段(数据量大时较慢)"
            onClick={() => onChange({ ...value, max_points: 0 }, true)}
          >
            投射全部{total ? ` (${total})` : ""}
          </button>
        </div>
      ) : null}
      {(value.max_points ?? PROJ_DEFAULTS.max_points) === 0 ? (
        <button
          type="button"
          className="proj-all"
          title="恢复采样上限(4000),投射更快"
          onClick={() => onChange({ ...value, max_points: PROJ_DEFAULTS.max_points }, true)}
        >
          恢复采样 ({PROJ_DEFAULTS.max_points})
        </button>
      ) : null}
    </section>
  );
}
