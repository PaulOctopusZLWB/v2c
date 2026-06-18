import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { ViewpointState } from "../../api/types";
import { dayLabel, sessionListLabel } from "../../lib/format";
import { Icon } from "../../components/Icon";
import { TranscriptEditor } from "./TranscriptEditor";
import { PromptEditor } from "./PromptEditor";
import { ResultEditor } from "./ResultEditor";

const POLL_MS = 2000;

type SessionRow = { session_id: string; started_at: string; segment_count: number; review_status: string; name?: string | null };

/**
 * The per-session 观点 workspace: a day/session picker on top, then a 2-column grid — the
 * editable transcript on the left, the editable prompt + result on the right. The single source
 * of truth is the loaded `ViewpointState`; after any edit we re-fetch it so `stale`/`status`
 * stay correct, and while it's `generating` we poll every ~2s until it settles.
 */
export function ViewpointWorkspace({
  initialDay,
  onPlaybackError
}: {
  initialDay?: string | null;
  onPlaybackError?: (message: string) => void;
} = {}) {
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [day, setDay] = useState<string>(initialDay ?? "");
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [vp, setVp] = useState<ViewpointState | null>(null);
  const [loading, setLoading] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api.days().then((r) => setDays(r.days ?? [])).catch(() => setDays([]));
  }, []);

  // Load a day's sessions whenever the day changes.
  useEffect(() => {
    if (!day) { setSessions([]); return; }
    let cancelled = false;
    api
      .sessionsForDay(day)
      .then((r) => { if (!cancelled) setSessions(r.sessions ?? []); })
      .catch(() => { if (!cancelled) setSessions([]); });
    return () => { cancelled = true; };
  }, [day]);

  // Clear any pending poll on unmount.
  useEffect(() => () => { if (pollTimer.current) clearTimeout(pollTimer.current); }, []);

  // Fetch the viewpoint for `id`. If it's generating, schedule the next poll; otherwise stop.
  const loadViewpoint = async (id: string) => {
    if (pollTimer.current) { clearTimeout(pollTimer.current); pollTimer.current = null; }
    const state = await api.viewpoint(id);
    setVp(state);
    if (state.generating) {
      pollTimer.current = setTimeout(() => { void loadViewpoint(id); }, POLL_MS);
    }
  };

  const pickSession = async (id: string) => {
    setSessionId(id);
    setVp(null);
    if (!id) return;
    setLoading(true);
    try {
      await loadViewpoint(id);
    } finally {
      setLoading(false);
    }
  };

  // Re-fetch after any edit so stale/status stay correct (and resume polling if needed).
  const refetch = () => { if (sessionId) void loadViewpoint(sessionId); };

  const generate = async () => {
    if (!sessionId) return;
    await api.generateViewpoint(sessionId);
    // Re-load immediately so `generating` flips true and polling starts.
    await loadViewpoint(sessionId);
  };

  return (
    <div className="vp-page">
      <div className="vp-picker card">
        <label className="vp-pick">
          <span>日期</span>
          <select
            aria-label="观点日期"
            value={day}
            disabled={days.length === 0}
            onChange={(e) => { setDay(e.target.value); void pickSession(""); }}
          >
            <option value="" disabled>{days.length === 0 ? "暂无有数据的日期" : "选择日期…"}</option>
            {days.map((d) => (
              <option key={d.day} value={d.day}>{dayLabel(d.day)} · {d.session_count} 场</option>
            ))}
          </select>
        </label>
        <label className="vp-pick">
          <span>会话</span>
          <select
            aria-label="观点会话"
            value={sessionId}
            disabled={!day || sessions.length === 0}
            onChange={(e) => void pickSession(e.target.value)}
          >
            <option value="" disabled>{!day ? "先选日期" : sessions.length === 0 ? "该日无会话" : "选择会话…"}</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>{sessionListLabel(s)}</option>
            ))}
          </select>
        </label>
      </div>

      {vp ? (
        <div className="viewpoint-workspace">
          <div className="vp-left">
            <TranscriptEditor
              segments={vp.segments}
              stale={vp.stale}
              onChanged={refetch}
              onPlaybackError={onPlaybackError}
            />
          </div>
          <div className="vp-right">
            <PromptEditor sessionId={vp.session_id} prompt={vp.prompt} onChanged={refetch} />
            <ResultEditor vp={vp} onChanged={refetch} onGenerate={() => void generate()} />
          </div>
        </div>
      ) : loading ? (
        <div className="vp-loading" role="status">
          <span className="spinner" aria-hidden /> 正在载入会话…
        </div>
      ) : (
        <div className="empty">
          <Icon name="inbox" className="empty-icon" />
          <h3>选择一个会话开始</h3>
          <p>选好日期与会话后,可逐段编辑转写、生成并保存观点。</p>
        </div>
      )}
    </div>
  );
}
