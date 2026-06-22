import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { PersonRow, ProjectionPoint, ProjectionRequest } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { emotionColor, emotionMeta } from "../../lib/emotionColors";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { Icon } from "../../components/Icon";

type ColorMode = "person" | "session" | "emotion";

export type VoiceprintMapState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "ready"; pointCount: number; capped: boolean; total?: number };

/** Default emotion for a point with no extracted emotion row (so it still draws a colour). */
const DEFAULT_EMOTION = "中立/neutral";

/** A stable short label for a session id (last 6 chars when long), for the 会话 legend. */
function sessionKey(p: ProjectionPoint): string {
  return p.session_id ?? "未知会话";
}

/** Points with no person attribution collapse to ONE 未识别 group (a distinct neutral hue), so the
 *  gate's remaining work is visible spatially — assigned points keep their person colour. */
const UNASSIGNED_KEY = "__未识别__";
const UNASSIGNED_COLOR = "#8b93a7";

/** The stable cluster key for a point: the labelled person, else the shared 未识别 bucket. */
function clusterKey(p: ProjectionPoint): string {
  return p.person_id ?? UNASSIGNED_KEY;
}

/** Human label for a cluster key: the person, else 未识别. */
function clusterLabel(p: ProjectionPoint): string {
  return p.person_id ? p.person_label ?? p.person_id : "未识别";
}

/** Colour for a person/cluster key — fixed grey for the 未识别 bucket, generated hue otherwise. */
function keyColor(key: string): string {
  return key === UNASSIGNED_KEY ? UNASSIGNED_COLOR : speakerColor(key);
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
  request,
  onResult,
  onState,
  onSelectionChange,
  onPlaybackError,
  people,
  onLabel,
  onChanged
}: {
  /** The multi-scope, tunable projection to fetch + render. `null` → the pick/empty state.
   *  The parent rebuilds this only on 投射 / scope / method change, so dragging a slider in
   *  ProjectionControls never refetches here. */
  request: ProjectionRequest | null;
  /** Report each result's subsample state up (lets the parent's controls show the capped note). */
  onResult?: (r: { capped: boolean; n: number; total: number } | null) => void;
  onState?: (state: VoiceprintMapState) => void;
  onSelectionChange?: (count: number) => void;
  onPlaybackError?: (message: string) => void;
  /** When provided alongside onLabel, enables a 框选 (lasso) → 标注 teaching toolbar. */
  people?: PersonRow[];
  /** Commit the selected segments to a person; resolve to refetch colours. */
  onLabel?: (personId: string, segmentIds: string[]) => Promise<unknown> | void;
  /** Notify the parent after a successful label (e.g. to reload the People panel). */
  onChanged?: () => void;
}) {
  const audio = useSegmentAudio();
  const [points, setPoints] = useState<ProjectionPoint[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Subsampling note from the last result (the scope had more points than max_points).
  const [capped, setCapped] = useState<{ n: number; total: number } | null>(null);
  // Keep onResult out of the fetch effect's deps (it may change identity each render).
  const onResultRef = useRef(onResult);
  useEffect(() => { onResultRef.current = onResult; }, [onResult]);
  const onStateRef = useRef(onState);
  useEffect(() => { onStateRef.current = onState; }, [onState]);
  const reportState = useCallback((state: VoiceprintMapState) => {
    onStateRef.current?.(state);
  }, []);

  // Colour mode: by person/speaker cluster (default) or by acoustic emotion. In 情绪 mode the
  // per-segment dominant-emotion labels are fetched for the scope and drive both the point
  // colours and the legend (emotion classes instead of person keys).
  const [colorMode, setColorMode] = useState<ColorMode>("person");
  const colorModeRef = useRef<ColorMode>("person");
  useEffect(() => { colorModeRef.current = colorMode; }, [colorMode]);
  const [emotionLabels, setEmotionLabels] = useState<Record<string, string>>({});
  const emotionLabelsRef = useRef<Record<string, string>>({});
  useEffect(() => { emotionLabelsRef.current = emotionLabels; }, [emotionLabels]);

  // Lasso-to-label is only offered when the parent wires both people + onLabel.
  const canLabel = !!people && !!onLabel;
  const [selectMode, setSelectMode] = useState(false);
  const selectModeRef = useRef(false);
  useEffect(() => { selectModeRef.current = selectMode; }, [selectMode]);
  // The committed selection (segment ids) drives the toolbar + a highlight in draw().
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const selectedIdsRef = useRef<Set<string>>(selectedIds);
  useEffect(() => { selectedIdsRef.current = selectedIds; }, [selectedIds]);
  const onSelectionChangeRef = useRef(onSelectionChange);
  useEffect(() => { onSelectionChangeRef.current = onSelectionChange; }, [onSelectionChange]);
  useEffect(() => { onSelectionChangeRef.current?.(selectedIds.size); }, [selectedIds.size]);
  const [labelPersonId, setLabelPersonId] = useState("");
  // Live rubber-band rectangle in canvas pixels while dragging a selection.
  const rectRef = useRef<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [rect, setRect] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

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

  // hover + playingId drive a per-frame ring in draw(); mirror them into refs so draw() can
  // read the latest value WITHOUT being in its dependency array (keeping draw referentially
  // stable, so resize() and the ResizeObserver effect don't churn on every mouse move).
  const [hover, setHover] = useState<{ point: ProjectionPoint; x: number; y: number } | null>(null);
  const hoverRef = useRef(hover);
  useEffect(() => { hoverRef.current = hover; }, [hover]);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const playingIdRef = useRef(playingId);
  useEffect(() => { playingIdRef.current = playingId; }, [playingId]);

  // A stable scope signature so the emotion-labels effect (and any memo) only re-runs when the
  // actual session/day union changes — not on every new request object identity.
  const scopeKey = useMemo(() => {
    if (!request) return "";
    const s = [...(request.session_ids ?? [])].sort();
    const d = [...(request.days ?? [])].sort();
    return `s:${s.join(",")}|d:${d.join(",")}`;
  }, [request]);

  // --- fetch the projection whenever the request changes (parent rebuilds it on 投射/scope/
  //     method — never on a slider drag, so this stays cheap). `null` → empty pick state. ---
  useEffect(() => {
    setHover(null);
    setFocusKey(null);
    setSelectedIds(new Set());
    rectRef.current = null;
    setRect(null);
    viewRef.current = { ...IDENTITY };
    if (!request) {
      setPoints(null);
      setError(null);
      setCapped(null);
      onResultRef.current?.(null);
      setLoading(false);
      reportState({ status: "idle" });
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    reportState({ status: "loading" });
    api
      .projection(request)
      .then((res) => {
        if (cancelled) return;
        const pts = res.points ?? [];
        const n = res.n ?? pts.length;
        const total = res.total_in_scope ?? 0;
        setSelectedIds(new Set());
        setPoints(pts);
        setCapped(res.capped ? { n, total } : null);
        onResultRef.current?.({ capped: !!res.capped, n, total });
        setLoading(false);
        if (pts.length === 0) reportState({ status: "empty" });
        else reportState({ status: "ready", pointCount: pts.length, capped: !!res.capped, total });
      })
      .catch((err) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "投影失败";
        setError(message);
        setPoints([]);
        setCapped(null);
        onResultRef.current?.(null);
        setLoading(false);
        reportState({ status: "error", message });
      });
    return () => { cancelled = true; };
  }, [request, reportState]);

  // --- fetch per-segment emotion labels when in 情绪 mode (lazily, on scope/mode change) ---
  // The labels endpoint is single-scope, so over a multi-scope request we fetch per session +
  // per day and merge the maps. Keyed off scopeKey so it doesn't re-run on param-only changes.
  useEffect(() => {
    if (colorMode !== "emotion" || !request) return;
    let cancelled = false;
    const scopes: Array<{ session_id?: string | null; day?: string | null }> = [
      ...(request.session_ids ?? []).map((id) => ({ session_id: id })),
      ...(request.days ?? []).map((d) => ({ day: d }))
    ];
    if (scopes.length === 0) scopes.push({});
    Promise.all(scopes.map((s) => api.emotionLabels(s).then((r) => r.labels ?? {}).catch(() => ({}))))
      .then((maps) => {
        if (cancelled) return;
        const merged: Record<string, string> = {};
        for (const m of maps) Object.assign(merged, m);
        setEmotionLabels(merged);
      });
    return () => { cancelled = true; };
  }, [colorMode, scopeKey, request]);

  // The focus/colour key for a point: emotion class in 情绪 mode, session id in 会话 mode, else
  // the person/speaker cluster.
  const keyOf = useCallback(
    (p: ProjectionPoint): string =>
      colorMode === "emotion"
        ? (emotionLabels[p.segment_id] ?? DEFAULT_EMOTION)
        : colorMode === "session"
          ? sessionKey(p)
          : clusterKey(p),
    [colorMode, emotionLabels]
  );

  // --- legend: distinct cluster keys (人物) / emotion classes (情绪) / session ids (会话) ---
  const legend = useMemo(() => {
    if (colorMode === "emotion") {
      const by = new Map<string, { key: string; label: string; color: string; emoji: string; count: number }>();
      for (const p of points ?? []) {
        const label = emotionLabels[p.segment_id] ?? DEFAULT_EMOTION;
        const meta = emotionMeta(label);
        const entry = by.get(label) ?? { key: label, label: meta.zh, color: meta.color, emoji: meta.emoji, count: 0 };
        entry.count += 1;
        by.set(label, entry);
      }
      return Array.from(by.values()).sort((a, b) => b.count - a.count);
    }
    if (colorMode === "session") {
      const by = new Map<string, { key: string; label: string; color: string; emoji: string; count: number }>();
      for (const p of points ?? []) {
        const key = sessionKey(p);
        const entry = by.get(key) ?? { key, label: key, color: speakerColor(key), emoji: "", count: 0 };
        entry.count += 1;
        by.set(key, entry);
      }
      return Array.from(by.values()).sort((a, b) => b.count - a.count);
    }
    const by = new Map<string, { key: string; label: string; color: string; emoji: string; count: number }>();
    for (const p of points ?? []) {
      const key = clusterKey(p);
      const entry = by.get(key) ?? { key, label: clusterLabel(p), color: keyColor(key), emoji: "", count: 0 };
      entry.count += 1;
      by.set(key, entry);
    }
    return Array.from(by.values()).sort((a, b) => b.count - a.count);
  }, [points, colorMode, emotionLabels]);

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

    const selected = selectedIdsRef.current;
    const mode = colorModeRef.current;
    const emoLabels = emotionLabelsRef.current;
    const r = 3.4;
    for (const p of pts) {
      const { px, py } = project(p);
      // The "key" governs both colour and focus; emotion class in 情绪, session id in 会话.
      const key = mode === "emotion" ? (emoLabels[p.segment_id] ?? DEFAULT_EMOTION) : mode === "session" ? sessionKey(p) : clusterKey(p);
      const dim = focus !== null && key !== focus;
      const color = mode === "emotion" ? emotionColor(key) : mode === "session" ? speakerColor(key) : keyColor(key);
      const isSel = selected.has(p.segment_id);
      ctx.globalAlpha = dim ? 0.08 : isSel ? 0.95 : 0.78;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(px, py, isSel ? r + 1.2 : r, 0, Math.PI * 2);
      ctx.fill();
      if (isSel) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "rgba(230, 237, 246, 0.95)";
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.arc(px, py, r + 3, 0, Math.PI * 2);
        ctx.stroke();
      }
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
    ring(playingIdRef.current, "rgba(45, 212, 238, 0.9)", 2.2);
    ring(hoverRef.current?.point.segment_id ?? null, "rgba(230, 237, 246, 0.85)", 1.6);

    // live rubber-band selection rectangle.
    const rb = rectRef.current;
    if (rb) {
      const x = Math.min(rb.x0, rb.x1);
      const y = Math.min(rb.y0, rb.y1);
      const rw = Math.abs(rb.x1 - rb.x0);
      const rh = Math.abs(rb.y1 - rb.y0);
      ctx.save();
      ctx.fillStyle = "rgba(45, 212, 238, 0.10)";
      ctx.strokeStyle = "rgba(45, 212, 238, 0.85)";
      ctx.lineWidth = 1.2;
      ctx.fillRect(x, y, rw, rh);
      ctx.strokeRect(x, y, rw, rh);
      ctx.restore();
    }
    // draw() reads all transient/per-frame state (hover, playingId, selectedIds, rect,
    // colorMode, emotionLabels, view, focus) from refs, so only `points` belongs here. This
    // keeps draw()/resize() referentially stable across pointer moves; redraws are triggered
    // imperatively from the pointer/zoom/pan handlers and the state-mirroring effect below.
  }, [points]);

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
    // Subscribe ONCE (resize is stable) and only reassign the backing store on an ACTUAL
    // size change — otherwise a stale observer fire would needlessly realloc + clear the canvas.
    const ro = new ResizeObserver(() => {
      const box2 = boxRef.current;
      if (!box2) return;
      const r = box2.getBoundingClientRect();
      const w = r.width || 640;
      const h = r.height || 420;
      if (w === sizeRef.current.w && h === sizeRef.current.h) { draw(); return; }
      resize();
    });
    ro.observe(box);
    return () => ro.disconnect();
  }, [resize, draw]);

  // Redraw whenever rendered state changes. draw() is referentially stable (reads transient
  // state from refs), so we list the state values here to schedule a redraw without ever
  // reallocating the canvas. Per-frame interactions (hover/pan/zoom/lasso) redraw imperatively
  // from their handlers; this covers state-driven changes (focus, colour mode, selection, …).
  useEffect(() => {
    draw();
  }, [draw, focusKey, colorMode, emotionLabels, selectedIds, rect, hover, playingId]);

  // --- hit-testing: nearest point within a few px of the cursor (linear scan) ---
  const hitTest = useCallback((cx: number, cy: number): ProjectionPoint | null => {
    const { w, h } = sizeRef.current;
    const view = viewRef.current;
    const pts = points ?? [];
    const focus = focusRef.current;
    let best: ProjectionPoint | null = null;
    let bestD = 10 * 10; // within ~10px
    for (const p of pts) {
      if (focus !== null && keyOf(p) !== focus) continue;
      const px = p.x * w * view.scale + view.tx;
      const py = (1 - p.y) * h * view.scale + view.ty;
      const d = (px - cx) * (px - cx) + (py - cy) * (py - cy);
      if (d < bestD) { bestD = d; best = p; }
    }
    return best;
  }, [points, keyOf]);

  // All points whose projected pixel falls inside the (canvas-space) rectangle.
  const pointsInRect = useCallback((box: { x0: number; y0: number; x1: number; y1: number }): string[] => {
    const { w, h } = sizeRef.current;
    const view = viewRef.current;
    const focus = focusRef.current;
    const minX = Math.min(box.x0, box.x1);
    const maxX = Math.max(box.x0, box.x1);
    const minY = Math.min(box.y0, box.y1);
    const maxY = Math.max(box.y0, box.y1);
    const ids: string[] = [];
    for (const p of points ?? []) {
      if (focus !== null && keyOf(p) !== focus) continue; // respect an active focus
      const px = p.x * w * view.scale + view.tx;
      const py = (1 - p.y) * h * view.scale + view.ty;
      if (px >= minX && px <= maxX && py >= minY && py <= maxY) ids.push(p.segment_id);
    }
    return ids;
  }, [points, keyOf]);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    // Select mode: extend the rubber-band rectangle while dragging.
    if (selectModeRef.current && rectRef.current) {
      rectRef.current = { ...rectRef.current, x1: cx, y1: cy };
      setRect(rectRef.current);
      draw();
      return;
    }
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
    // Select mode: begin a rubber-band rectangle (no play/pan).
    if (selectModeRef.current) {
      rectRef.current = { x0: cx, y0: cy, x1: cx, y1: cy };
      setRect(rectRef.current);
      setHover(null);
      canvasRef.current?.setPointerCapture?.(e.pointerId);
      return;
    }
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
    // Select mode: commit the rectangle to a selection, then clear the rubber-band.
    if (selectModeRef.current && rectRef.current) {
      const ids = pointsInRect(rectRef.current);
      setSelectedIds(new Set(ids));
      rectRef.current = null;
      setRect(null);
      canvasRef.current?.releasePointerCapture?.(e.pointerId);
      return;
    }
    dragRef.current = null;
    canvasRef.current?.releasePointerCapture?.(e.pointerId);
  }, [pointsInRect]);

  // Wheel = zoom the map ONLY. React's onWheel is registered passive (preventDefault is a no-op,
  // so the page/column also scrolled — the "four meanings" bug), so bind a NATIVE non-passive
  // listener that can actually preventDefault and stop the scroll from bubbling.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
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
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [draw]);

  const resetView = useCallback(() => {
    viewRef.current = { ...IDENTITY };
    setHover(null);
    draw();
  }, [draw]);

  const toggleFocus = (key: string) => setFocusKey((cur) => (cur === key ? null : key));

  // Switching colour mode changes what the legend keys mean, so any active focus is cleared.
  const switchColorMode = (mode: ColorMode) => {
    setColorMode((cur) => {
      if (cur !== mode) setFocusKey(null);
      return mode;
    });
    setHover(null);
  };

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
    rectRef.current = null;
    setRect(null);
  }, []);

  const [labeling, setLabeling] = useState(false);
  const commitLabel = useCallback(async () => {
    if (!onLabel || !labelPersonId || selectedIds.size === 0) return;
    setLabeling(true);
    try {
      await onLabel(labelPersonId, Array.from(selectedIds));
      clearSelection();
      // Refetch the projection so the just-labelled points recolour to the person.
      if (request) {
        const res = await api.projection(request);
        setPoints(res.points ?? []);
        setCapped(res.capped ? { n: res.n ?? res.points?.length ?? 0, total: res.total_in_scope ?? 0 } : null);
      }
      onChanged?.();
    } finally {
      setLabeling(false);
    }
  }, [onLabel, labelPersonId, selectedIds, clearSelection, request, onChanged]);

  const n = points?.length ?? 0;

  return (
    <section className="voiceprint-map card">
      <div className="vmap-toolbar">
        <div className="section-title" style={{ margin: 0 }}>
          <Icon name="mic" /> 声纹云图
        </div>
        <div className="vmap-actions">
          <div className="vmap-colormode" role="group" aria-label="着色">
            <span className="vmap-colormode-label">着色:</span>
            <button
              type="button"
              className={colorMode === "person" ? "active" : ""}
              aria-pressed={colorMode === "person"}
              onClick={() => switchColorMode("person")}
            >
              人物
            </button>
            <button
              type="button"
              className={colorMode === "session" ? "active" : ""}
              aria-pressed={colorMode === "session"}
              title="按会话着色 — 跨会话对比"
              onClick={() => switchColorMode("session")}
            >
              会话
            </button>
            <button
              type="button"
              className={colorMode === "emotion" ? "active" : ""}
              aria-pressed={colorMode === "emotion"}
              title="按情绪着色"
              onClick={() => switchColorMode("emotion")}
            >
              情绪
            </button>
          </div>
          {canLabel ? (
            <button
              type="button"
              className={`ghost${selectMode ? " active" : ""}`}
              aria-pressed={selectMode}
              onClick={() => {
                setSelectMode((v) => {
                  const next = !v;
                  if (!next) clearSelection();
                  return next;
                });
                setHover(null);
              }}
              title="框选地图上的点以标注为某人"
            >
              <Icon name="person" /> 框选
            </button>
          ) : null}
          <button type="button" className="ghost" onClick={resetView} title="重置视图">
            <Icon name="refresh" /> 重置视图
          </button>
        </div>
      </div>

      {canLabel && selectMode ? (
        <div className="vmap-select-toolbar" role="group" aria-label="标注选中">
          <span className="vmap-select-count num">{`已选 ${selectedIds.size} 点`}</span>
          <select
            aria-label="标注为"
            value={labelPersonId}
            disabled={labeling}
            onChange={(e) => setLabelPersonId(e.target.value)}
          >
            <option value="" disabled>选择人物…</option>
            {(people ?? []).map((p) => (
              <option key={p.person_id} value={p.person_id}>
                {p.person_type === "non_speaker" ? `非发言人 · ${p.display_name}` : p.display_name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="primary"
            onClick={() => void commitLabel()}
            disabled={labeling || selectedIds.size === 0 || !labelPersonId}
            aria-busy={labeling}
          >
            {labeling ? <span className="spinner" aria-hidden /> : <Icon name="accept" />}
            标注
          </button>
          <button
            type="button"
            className="ghost"
            onClick={clearSelection}
            disabled={labeling || selectedIds.size === 0}
          >
            清除选择
          </button>
        </div>
      ) : null}

      <div className="vmap-stage" ref={boxRef}>
        <canvas
          ref={canvasRef}
          className="vmap-canvas"
          onPointerMove={onPointerMove}
          onPointerDown={onPointerDown}
          onPointerUp={endDrag}
          onPointerLeave={(e) => { endDrag(e); setHover(null); }}
        />

        {loading ? (
          <div className="vmap-overlay" role="status">
            <span className="spinner" aria-hidden /> 正在投影声纹… 首次较慢
          </div>
        ) : error ? (
          <div className="vmap-overlay error" role="alert">投影失败:{error}</div>
        ) : !request ? (
          <div className="vmap-overlay" role="status">选择日期/会话后点「投射」</div>
        ) : n === 0 ? (
          <div className="vmap-overlay" role="status">该范围还没有声纹,请先在上方提取</div>
        ) : null}

        {capped && !loading ? (
          <div className="vmap-capped-note num" role="note" title="点数过多,已均匀采样以保持流畅">
            已采样 {capped.n}/{capped.total}
          </div>
        ) : null}

        {hover && !loading ? (
          <div
            className="vmap-tooltip"
            style={{ left: hover.x, top: hover.y }}
            role="tooltip"
          >
            <div className="vmap-tip-head">
              <span
                className="vmap-swatch"
                style={{
                  background:
                    colorMode === "emotion"
                      ? emotionColor(emotionLabels[hover.point.segment_id] ?? DEFAULT_EMOTION)
                      : colorMode === "session"
                        ? speakerColor(sessionKey(hover.point))
                        : keyColor(clusterKey(hover.point))
                }}
              />
              {colorMode === "emotion"
                ? `${emotionMeta(emotionLabels[hover.point.segment_id] ?? DEFAULT_EMOTION).emoji} ${clusterLabel(hover.point)}`
                : clusterLabel(hover.point)}
            </div>
            {colorMode === "session" && hover.point.session_id ? (
              <div className="vmap-tip-meta muted">{sessionKey(hover.point)}</div>
            ) : null}
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
                  <span className="vmap-swatch" style={{ background: c.color }} />
                  {c.emoji ? <span className="vmap-legend-emoji" aria-hidden>{c.emoji}</span> : null}
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
