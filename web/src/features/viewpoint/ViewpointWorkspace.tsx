import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { useDaysQuery, useSessionsForDayQuery } from "../../api/hooks";
import type { ViewpointState } from "../../api/types";
import { dayLabel, sessionListLabel } from "../../lib/format";
import { EmptyState, Skeleton } from "../../components/ui";
import { TranscriptEditor } from "./TranscriptEditor";
import { PromptEditor } from "./PromptEditor";
import { ResultEditor } from "./ResultEditor";

const POLL_MS = 2000;

/**
 * The per-session 观点 workspace: a day/session picker on top, then a 2-column grid — the
 * editable transcript on the left, the editable prompt + result on the right. The single source
 * of truth is the loaded `ViewpointState`; after any edit we re-fetch it so `stale`/`status`
 * stay correct, and while it's `generating` we poll every ~2s until it settles.
 */
export function ViewpointWorkspace({
  initialDay,
  initialSessionId,
  onPlaybackError,
  confirm
}: {
  initialDay?: string | null;
  initialSessionId?: string | null;
  onPlaybackError?: (message: string) => void;
  // App 的危险确认对话框,透传给 ResultEditor 的「重新生成」守卫。
  confirm?: import("../../components/ui/Dialog").ConfirmFn;
} = {}) {
  const [day, setDay] = useState<string>(initialDay ?? "");
  const [sessionId, setSessionId] = useState<string>("");
  const [vp, setVp] = useState<ViewpointState | null>(null);
  const [loading, setLoading] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const daysQuery = useDaysQuery();
  const days = daysQuery.data?.days ?? [];
  const sessionsQuery = useSessionsForDayQuery(day);
  const sessions = sessionsQuery.data?.sessions ?? [];
  const sessionsLoading = !!day && sessionsQuery.isLoading;

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

  useEffect(() => {
    if (initialDay && initialDay !== day) setDay(initialDay);
  }, [initialDay]);

  useEffect(() => {
    if (initialSessionId && initialSessionId !== sessionId) void pickSession(initialSessionId);
  }, [initialSessionId]);

  return (
    <div className="vp-page">
      <div className="vp-picker card">
        <label className="vp-pick">
          <span>日期</span>
          <select
            aria-label="总结日期"
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
            aria-label="总结会话"
            value={sessionId}
            disabled={!day || sessionsLoading || sessions.length === 0}
            onChange={(e) => void pickSession(e.target.value)}
          >
            <option value="" disabled>{!day ? "先选日期" : sessionsLoading ? "正在载入会话" : sessions.length === 0 ? "该日无会话" : "选择会话…"}</option>
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
            <ResultEditor vp={vp} onChanged={refetch} onGenerate={() => void generate()} confirm={confirm} />
          </div>
        </div>
      ) : loading ? (
        <Skeleton label="正在载入会话" rows={3} className="vp-loading" />
      ) : (
        <EmptyState
          icon="inbox"
          title="选择一个会话开始"
          description="选好日期与会话后,可逐段编辑转写、生成并保存总结。"
        />
      )}
    </div>
  );
}
