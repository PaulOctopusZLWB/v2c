import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { ProjectionPoint } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { Icon } from "../../components/Icon";

type Method = "umap" | "pca";

/** The stable cluster key for a point: prefer the labelled person, else the raw speaker. */
function clusterKey(p: ProjectionPoint): string {
  return p.person_id ?? p.speaker ?? "未知";
}

/** Human label for a cluster key: prefer person_label, else the key itself. */
function clusterLabel(p: ProjectionPoint): string {
  return p.person_label ?? p.speaker ?? p.person_id ?? "未知";
}

interface View {
  scale: number;
  tx: number;
  ty: number;
}

const IDENTITY: View = { scale: 1, tx: 0, ty: 0 };

/**
 * 声纹云图 — the flagship "voiceprint cluster map". Projects stored CAM++ embeddings to a 2D
 * scatter (UMAP default, PCA quick-preview) that visibly clusters by speaker/person. Pure
 * canvas (devicePixelRatio-aware, no heavy deps): wheel zooms toward the cursor, drag pans,
 * hover highlights + tooltips the sentence, click plays the segment, the legend focuses a
 * cluster. Self-contained and lazy-friendly.
 */
export function VoiceprintMap({
  sessionId,
  day,
  onPlaybackError
}: {
  sessionId?: string | null;
  day?: string | null;
  onPlaybackError?: (message: string) => void;
}) {
  const audio = useSegmentAudio();
  const [method, setMethod] = useState<Method>("umap");
  const [points, setPoints] = useState<ProjectionPoint[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Imperative interaction state lives in refs (mutated on every pointer/wheel event); React
  // state only carries what the DOM (legend/tooltip) renders, so we don't re-render per frame.
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<View>({ ...IDENTITY });
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });
  const dragRef = useRef<{ x: number; y: number } | null>(null);

  const [focusKey, setFocusKey] = useState<string | null>(null);
  const focusRef = useRef<string | null>(null);
  useEffect(() => { focusRef.current = focusKey; }, [focusKey]);

  const [hover, setHover] = useState<{ point: ProjectionPoint; x: number; y: number } | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);

  // --- fetch the projection on scope/method change ---
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setHover(null);
    setFocusKey(null);
    viewRef.current = { ...IDENTITY };
    api
      .embeddingProjection({ session_id: sessionId ?? null, day: day ?? null, method })
      .then((res) => {
        if (cancelled) return;
        setPoints(res.points ?? []);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "投影失败");
        setPoints([]);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sessionId, day, method]);

  // --- legend: distinct cluster keys with color + count, ordered by descending count ---
  const legend = useMemo(() => {
    const by = new Map<string, { key: string; label: string; count: number }>();
    for (const p of points ?? []) {
      const key = clusterKey(p);
      const entry = by.get(key) ?? { key, label: clusterLabel(p), count: 0 };
      entry.count += 1;
      by.set(key, entry);
    }
    return Array.from(by.values()).sort((a, b) => b.count - a.count);
  }, [points]);

  // --- canvas drawing ---
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // jsdom has no real 2D backend: getContext returns null (and logs "Not implemented"). Guard
    // both the null and a throw so the component renders headless in tests without noise.
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      return;
    }
    if (!ctx) return;
    const { w, h } = sizeRef.current;
    if (w === 0 || h === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const view = viewRef.current;
    const focus = focusRef.current;
    const pts = points ?? [];

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    // subtle grid for depth
    ctx.save();
    ctx.strokeStyle = "rgba(80, 120, 160, 0.06)";
    ctx.lineWidth = 1;
    const step = 48;
    for (let gx = (view.tx % step + step) % step; gx < w; gx += step) {
      ctx.beginPath();
      ctx.moveTo(gx, 0);
      ctx.lineTo(gx, h);
      ctx.stroke();
    }
    for (let gy = (view.ty % step + step) % step; gy < h; gy += step) {
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w, gy);
      ctx.stroke();
    }
    ctx.restore();

    // map a normalized point [0,1] to view-space pixels (flip Y), with the {scale,tx,ty} transform.
    const project = (p: ProjectionPoint) => ({
      px: p.x * w * view.scale + view.tx,
      py: (1 - p.y) * h * view.scale + view.ty
    });

    const r = 3.4;
    for (const p of pts) {
      const { px, py } = project(p);
      const key = clusterKey(p);
      const dim = focus !== null && key !== focus;
      const color = speakerColor(key);
      ctx.globalAlpha = dim ? 0.08 : 0.78;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // highlight the playing + hovered point with a ring on top.
    const ring = (id: string | null, stroke: string, width: number) => {
      if (!id) return;
      const p = pts.find((q) => q.segment_id === id);
      if (!p) return;
      const { px, py } = project(p);
      ctx.strokeStyle = stroke;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.arc(px, py, r + 4, 0, Math.PI * 2);
      ctx.stroke();
    };
    ring(playingId, "rgba(45, 212, 238, 0.9)", 2.2);
    ring(hover?.point.segment_id ?? null, "rgba(230, 237, 246, 0.85)", 1.6);
  }, [points, hover, playingId]);

  // Resize the canvas backing store to its container (devicePixelRatio-scaled) and redraw.
  const resize = useCallback(() => {
    const canvas = canvasRef.current;
    const box = boxRef.current;
    if (!canvas || !box) return;
    const rect = box.getBoundingClientRect();
    const w = rect.width || 640;
    const h = rect.height || 420;
    const dpr = window.devicePixelRatio || 1;
    sizeRef.current = { w, h };
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    draw();
  }, [draw]);

  useEffect(() => {
    resize();
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => resize());
    ro.observe(box);
    return () => ro.disconnect();
  }, [resize]);

  // Redraw whenever the data / hover / focus / playing state changes.
  useEffect(() => { draw(); }, [draw, focusKey]);

  // --- hit-testing: nearest point within a few px of the cursor (linear scan) ---
  const hitTest = useCallback((cx: number, cy: number): ProjectionPoint | null => {
    const { w, h } = sizeRef.current;
    const view = viewRef.current;
    const pts = points ?? [];
    const focus = focusRef.current;
    let best: ProjectionPoint | null = null;
    let bestD = 10 * 10; // within ~10px
    for (const p of pts) {
      if (focus !== null && clusterKey(p) !== focus) continue;
      const px = p.x * w * view.scale + view.tx;
      const py = (1 - p.y) * h * view.scale + view.ty;
      const d = (px - cx) * (px - cx) + (py - cy) * (py - cy);
      if (d < bestD) { bestD = d; best = p; }
    }
    return best;
  }, [points]);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    if (dragRef.current) {
      viewRef.current.tx += cx - dragRef.current.x;
      viewRef.current.ty += cy - dragRef.current.y;
      dragRef.current = { x: cx, y: cy };
      setHover(null);
      draw();
      return;
    }
    const hit = hitTest(cx, cy);
    setHover(hit ? { point: hit, x: cx, y: cy } : null);
  }, [draw, hitTest]);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    // A click on a point plays it; otherwise begin a pan drag.
    const hit = hitTest(cx, cy);
    if (hit) {
      setPlayingId(hit.segment_id);
      void audio
        .play(hit.segment_id)
        .catch((err) => {
          setPlayingId((id) => (id === hit.segment_id ? null : id));
          onPlaybackError?.(err instanceof Error ? err.message : "音频播放失败");
        });
      return;
    }
    dragRef.current = { x: cx, y: cy };
    canvasRef.current?.setPointerCapture?.(e.pointerId);
  }, [audio, hitTest, onPlaybackError]);

  const endDrag = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    dragRef.current = null;
    canvasRef.current?.releasePointerCapture?.(e.pointerId);
  }, []);

  const onWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const view = viewRef.current;
    const factor = Math.exp(-e.deltaY * 0.0015);
    const next = Math.min(20, Math.max(0.4, view.scale * factor));
    const k = next / view.scale;
    // zoom toward the cursor: keep the point under the cursor fixed.
    view.tx = cx - (cx - view.tx) * k;
    view.ty = cy - (cy - view.ty) * k;
    view.scale = next;
    setHover(null);
    draw();
  }, [draw]);

  const resetView = useCallback(() => {
    viewRef.current = { ...IDENTITY };
    setHover(null);
    draw();
  }, [draw]);

  const toggleFocus = (key: string) => setFocusKey((cur) => (cur === key ? null : key));

  const n = points?.length ?? 0;

  return (
    <section className="voiceprint-map card">
      <div className="vmap-toolbar">
        <div className="section-title" style={{ margin: 0 }}>
          <Icon name="mic" /> 声纹云图
        </div>
        <div className="vmap-actions">
          <div className="vmap-method" role="group" aria-label="投影方法">
            <button
              type="button"
              className={method === "umap" ? "active" : ""}
              aria-pressed={method === "umap"}
              onClick={() => setMethod("umap")}
            >
              UMAP
            </button>
            <button
              type="button"
              className={method === "pca" ? "active" : ""}
              aria-pressed={method === "pca"}
              title="快速预览"
              onClick={() => setMethod("pca")}
            >
              PCA
            </button>
          </div>
          <button type="button" className="ghost" onClick={resetView} title="重置视图">
            <Icon name="refresh" /> 重置视图
          </button>
        </div>
      </div>

      <div className="vmap-stage" ref={boxRef}>
        <canvas
          ref={canvasRef}
          className="vmap-canvas"
          onPointerMove={onPointerMove}
          onPointerDown={onPointerDown}
          onPointerUp={endDrag}
          onPointerLeave={(e) => { endDrag(e); setHover(null); }}
          onWheel={onWheel}
        />

        {loading ? (
          <div className="vmap-overlay" role="status">
            <span className="spinner" aria-hidden /> 正在投影声纹… 首次较慢
          </div>
        ) : error ? (
          <div className="vmap-overlay error" role="alert">投影失败:{error}</div>
        ) : n === 0 ? (
          <div className="vmap-overlay" role="status">该范围还没有声纹,请先在上方提取</div>
        ) : null}

        {hover && !loading ? (
          <div
            className="vmap-tooltip"
            style={{ left: hover.x, top: hover.y }}
            role="tooltip"
          >
            <div className="vmap-tip-head">
              <span className="vmap-swatch" style={{ background: speakerColor(clusterKey(hover.point)) }} />
              {clusterLabel(hover.point)}
            </div>
            {hover.point.text ? <div className="vmap-tip-text">{hover.point.text}</div> : null}
          </div>
        ) : null}
      </div>

      {legend.length > 0 ? (
        <ul className="vmap-legend" role="list" aria-label="图例 — 按聚类聚焦">
          {legend.map((c) => {
            const focused = focusKey === c.key;
            const dimmed = focusKey !== null && !focused;
            return (
              <li key={c.key} className="vmap-legend-li" role="listitem">
                <button
                  type="button"
                  className={`vmap-legend-item${focused ? " focused" : ""}${dimmed ? " dimmed" : ""}`}
                  aria-pressed={focused}
                  onClick={() => toggleFocus(c.key)}
                  title={focused ? "取消聚焦" : "聚焦此聚类"}
                >
                  <span className="vmap-swatch" style={{ background: speakerColor(c.key) }} />
                  <span className="vmap-legend-label">{c.label}</span>
                  <span className="vmap-legend-count num">{c.count}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
