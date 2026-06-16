import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { EmbeddingStatus, LabelSegment, Person, ReclusterResult } from "../../api/types";
import { clockOfDay } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { Icon } from "../../components/Icon";

/**
 * 声纹 — voiceprint re-clustering. Diarization over-clusters; the user (1) extracts CAM++
 * embeddings for the scope, (2) labels a few anchor segments as specific persons, and
 * (3) re-clusters: anchors propagate to nearby segments by voiceprint similarity under a
 * threshold. Session-scoped — the segments endpoint requires a session.
 */
export function VoiceprintPanel({
  day,
  sessionId,
  persons,
  onCreatePerson,
  onPlaybackError
}: {
  day?: string | null;
  sessionId?: string | null;
  persons: Person[];
  onCreatePerson?: (name: string) => Promise<unknown> | void;
  onPlaybackError?: (message: string) => void;
}) {
  const scope = { session_id: sessionId ?? null, day: day ?? null };
  const audio = useSegmentAudio();

  // --- 1. coverage ---
  const [status, setStatus] = useState<EmbeddingStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function loadStatus() {
    setStatusError(null);
    try {
      setStatus(await api.embeddingStatus(scope));
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : "加载失败");
    }
  }

  useEffect(() => {
    void loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, day]);

  // Stop polling on unmount or scope change.
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, [sessionId, day]);

  const extract = useAsyncAction(async () => {
    await api.extractEmbeddings(scope);
    // Poll coverage every ~2s until nothing is pending; resolve only when done so the
    // button stays disabled (pending) for the whole pass.
    await new Promise<void>((resolve) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const next = await api.embeddingStatus(scope);
          setStatus(next);
          if (next.pending <= 0) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            resolve();
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          resolve();
        }
      }, 2000);
    });
  });

  // --- 2. anchors ---
  const [segments, setSegments] = useState<LabelSegment[]>([]);
  const [segError, setSegError] = useState<string | null>(null);
  const [anchors, setAnchors] = useState<Record<string, string>>({});

  useEffect(() => {
    setAnchors({});
    setSegments([]);
    if (!sessionId) return;
    setSegError(null);
    let cancelled = false;
    api
      .speakerSegments({ session_id: sessionId })
      .then((r) => { if (!cancelled) setSegments(r.segments ?? []); })
      .catch((err) => { if (!cancelled) setSegError(err instanceof Error ? err.message : "加载失败"); });
    return () => { cancelled = true; };
  }, [sessionId]);

  function setAnchor(segmentId: string, personId: string) {
    setAnchors((prev) => {
      const next = { ...prev };
      if (personId) next[segmentId] = personId;
      else delete next[segmentId];
      return next;
    });
  }

  // create-person affordance: prefer the prop, fall back to a local input.
  const [newName, setNewName] = useState("");
  const create = useAsyncAction(async (name: string) => {
    if (onCreatePerson) await onCreatePerson(name);
    else await api.createPerson(name);
    setNewName("");
  });

  const play = (segmentId: string) => {
    void audio.play(segmentId).catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed"));
  };

  // --- 3. recluster ---
  const [threshold, setThreshold] = useState(0.5);
  const [result, setResult] = useState<ReclusterResult | null>(null);
  const [reclusterError, setReclusterError] = useState<string | null>(null);
  const anchorCount = Object.keys(anchors).length;

  const recluster = useAsyncAction(async () => {
    setReclusterError(null);
    try {
      setResult(await api.recluster({ anchors, threshold, session_id: sessionId ?? null }));
    } catch (err) {
      setReclusterError(err instanceof Error ? err.message : "归类失败");
    }
  });

  const personName = (id: string) => persons.find((p) => p.person_id === id)?.display_name ?? id;

  return (
    <section className="voiceprint-panel card">
      {/* 1. coverage */}
      <div className="vp-section">
        <div className="section-title"><Icon name="mic" /> 声纹覆盖</div>
        {statusError ? <p className="muted" role="alert">{statusError}</p> : null}
        <div className="vp-coverage">
          <span>
            已提取 <span className="num">{status?.embedded ?? 0}</span>/<span className="num">{status?.total ?? 0}</span>
            {status && status.pending > 0 ? <span className="muted"> · 待提取 {status.pending}</span> : null}
          </span>
          <button
            className="primary"
            onClick={() => void extract.run()}
            disabled={extract.pending}
            aria-busy={extract.pending}
          >
            {extract.pending ? <span className="spinner" aria-hidden /> : <Icon name="mic" />}
            {extract.pending ? "正在提取…" : "提取声纹"}
          </button>
        </div>
      </div>

      {/* 2. anchors */}
      <div className="vp-section">
        <div className="section-title"><Icon name="person" /> 标注锚点 <span className="muted">已标注 {anchorCount}</span></div>
        {!sessionId ? (
          <p className="muted">请先选择一个会话以标注锚点片段。</p>
        ) : segError ? (
          <p className="muted" role="alert">{segError}</p>
        ) : segments.length === 0 ? (
          <p className="muted">该会话暂无可标注片段</p>
        ) : (
          <ul className="vp-seg-list">
            {segments.map((seg) => (
              <li className="vp-seg" key={seg.segment_id}>
                <button
                  className="icon-btn ghost"
                  aria-label={`播放 ${seg.segment_id}`}
                  title="播放"
                  onClick={() => play(seg.segment_id)}
                >
                  <Icon name="play" />
                </button>
                <span className="chip" style={{ background: speakerColor(seg.speaker) }}>{seg.speaker}</span>
                <span className="vp-time muted">{clockOfDay(seg.absolute_start_at)}</span>
                <span className="vp-seg-text">{seg.text}</span>
                <select
                  aria-label={`标注 ${seg.segment_id}`}
                  value={anchors[seg.segment_id] ?? ""}
                  onChange={(e) => setAnchor(seg.segment_id, e.target.value)}
                >
                  <option value="">未标注</option>
                  {persons.map((p) => (
                    <option key={p.person_id} value={p.person_id}>{p.display_name}</option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
        )}
        <div className="vp-add">
          <input
            aria-label="新建人物"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="新建人物"
            disabled={create.pending}
          />
          <button
            className="ghost"
            onClick={() => newName && void create.run(newName)}
            disabled={create.pending || !newName}
            aria-busy={create.pending}
          >
            {create.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
            {create.pending ? "正在新建…" : "新建人物"}
          </button>
        </div>
      </div>

      {/* 3. recluster */}
      <div className="vp-section">
        <div className="section-title"><Icon name="refresh" /> 重新归类</div>
        <div className="vp-threshold">
          <label htmlFor="vp-threshold">相似度阈值</label>
          <input
            id="vp-threshold"
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
          <span className="num">{threshold.toFixed(2)}</span>
        </div>
        <p className="muted vp-hint">提高阈值更严格(更多未定)、降低更宽松。</p>
        <button
          className="primary"
          onClick={() => void recluster.run()}
          disabled={recluster.pending || anchorCount === 0}
          aria-busy={recluster.pending}
        >
          {recluster.pending ? <span className="spinner" aria-hidden /> : <Icon name="refresh" />}
          {recluster.pending ? "正在归类…" : "重新归类"}
        </button>

        {reclusterError ? <p className="muted" role="alert">{reclusterError}</p> : null}
        {result ? (
          <div className="vp-result">
            <p>
              已归类 <span className="num">{result.assigned}</span>/<span className="num">{result.total}</span>
              {" · 未定 "}<span className="num">{result.unassigned}</span>
            </p>
            <ul className="vp-breakdown">
              {Object.entries(result.per_person).map(([personId, count]) => (
                <li key={personId}>
                  <span className="chip" style={{ background: speakerColor(personId) }}>{personName(personId)}</span>
                  <span className="num">{count}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}
