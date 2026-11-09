import { useRef, useState } from "react";
import { api } from "../../api/client";
import { useDaysQuery } from "../../api/hooks";
import { dayLabel, timeOfDay } from "../../lib/format";
import { Icon } from "../../components/Icon";
import { Skeleton } from "../../components/ui";

/** The 声纹 projection scope: a union of whole days and/or individual sessions. */
export interface Scope {
  session_ids: string[];
  days: string[];
}

type SessionRow = {
  session_id: string;
  started_at: string;
  segment_count: number;
  review_status: string;
  /** Optional friendly name (may be null for now). */
  name?: string | null;
};

function localDay(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * 范围选择 — whole days and their individual sessions form one deduplicated selection.
 * Inspecting a session can also switch the independent identity review context.
 */
export function ScopeSelector({
  value,
  onChange,
  activeSessionId,
  activeSessionDay,
  onInspectSession
}: {
  value: Scope;
  onChange: (v: Scope) => void;
  activeSessionId?: string | null;
  activeSessionDay?: string | null;
  onInspectSession?: (sessionId: string, day: string) => void;
}) {
  const daysQuery = useDaysQuery();
  const days = daysQuery.data?.days ?? [];
  const loading = daysQuery.isLoading;
  const today = new Date();
  const cutoff = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
  const recent = days.filter((d) => d.day >= localDay(cutoff) && d.day <= localDay(today));
  const older = days.filter((d) => d.day < localDay(cutoff) || d.day > localDay(today));
  // Which day rows are expanded, and their lazily-fetched sessions (cached after first open).
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sessionsByDay, setSessionsByDay] = useState<Record<string, SessionRow[]>>({});
  const [loadingDays, setLoadingDays] = useState<Set<string>>(new Set());
  const [scopeError, setScopeError] = useState<string | null>(null);
  const valueRef = useRef(value);
  const clearRevision = useRef(0);
  valueRef.current = value;

  const toggleExpand = (day: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(day)) {
        next.delete(day);
        return next;
      }
      next.add(day);
      // Lazy-load this day's sessions the first time it's opened.
      if (!sessionsByDay[day]) {
        api
          .sessionsForDay(day)
          .then((r) => setSessionsByDay((m) => ({ ...m, [day]: r.sessions ?? [] })))
          .catch(() => setScopeError("日期中的会话读取失败，请重试。"));
      }
      return next;
    });
  };

  const toggleDay = async (day: string, sessionCount: number, checked: boolean) => {
    setScopeError(null);
    const revision = clearRevision.current;
    let sessions = sessionsByDay[day];
    if (!sessions) {
      setLoadingDays((previous) => new Set(previous).add(day));
      try {
        sessions = (await api.sessionsForDay(day)).sessions ?? [];
        setSessionsByDay((previous) => ({ ...previous, [day]: sessions! }));
      } catch {
        setScopeError("日期中的会话读取失败，请重试。");
        return;
      } finally {
        setLoadingDays((previous) => { const next = new Set(previous); next.delete(day); return next; });
      }
    }
    if (revision !== clearRevision.current) return;
    const sessionIds = new Set(sessions.map((s) => s.session_id));
    const current = valueRef.current;
    const next = {
      days: checked ? [...current.days.filter((d) => d !== day), day] : current.days.filter((d) => d !== day),
      session_ids: current.session_ids.filter((id) => !sessionIds.has(id))
    };
    valueRef.current = next;
    onChange(next);
    if (checked && sessionCount === 1 && sessions[0]) onInspectSession?.(sessions[0].session_id, day);
  };
  const toggleSession = (sid: string, day: string) => {
    const sessions = sessionsByDay[day] ?? [];
    const current = valueRef.current;
    const daySelected = current.days.includes(day);
    const selected = new Set(current.session_ids);
    if (daySelected) {
      for (const session of sessions) selected.add(session.session_id);
      selected.delete(sid);
    } else if (selected.has(sid)) selected.delete(sid);
    else selected.add(sid);
    const next = { days: current.days.filter((d) => d !== day), session_ids: [...selected] };
    valueRef.current = next;
    onChange(next);
    onInspectSession?.(sid, day);
  };
  const clear = () => { clearRevision.current++; valueRef.current = { session_ids: [], days: [] }; onChange(valueRef.current); };

  const total = value.days.length + value.session_ids.length;

  const renderDays = (rows: typeof days) => rows.map((d) => {
            const open = expanded.has(d.day);
            const sessions = sessionsByDay[d.day];
            const countSelected = sessions?.filter((s) => value.session_ids.includes(s.session_id)).length ??
              (activeSessionDay === d.day && !!activeSessionId && value.session_ids.includes(activeSessionId) ? 1 : 0);
            const dayChecked = value.days.includes(d.day) || (!!d.session_count && countSelected === d.session_count);
            const partial = !dayChecked && countSelected > 0;
            return (
              <li key={d.day} className="scope-day">
                <div className="scope-day-row">
                  <label className="scope-check">
                    <input
                      type="checkbox"
                      aria-label={dayLabel(d.day)}
                      checked={dayChecked}
                      ref={(element) => { if (element) element.indeterminate = partial; }}
                      disabled={loadingDays.has(d.day)}
                      onChange={(e) => void toggleDay(d.day, d.session_count, e.target.checked)}
                    />
                    <span className="scope-day-label">{dayLabel(d.day)}</span>
                    <span className="scope-day-count num">{d.session_count}</span>
                  </label>
                  <button type="button" className={`scope-expander${open ? " open" : ""}`}
                    aria-label={`展开 ${d.day}`} aria-expanded={open} onClick={() => toggleExpand(d.day)}
                    title={open ? "收起会话" : "展开会话"}><Icon name="chevron" /></button>
                </div>
                {open ? (
                  <ul className="scope-sessions" role="list">
                    {sessions === undefined ? (
                      <li className="scope-session-loading muted"><span className="spinner" aria-hidden /> 载入会话…</li>
                    ) : sessions.length === 0 ? (
                      <li className="muted scope-session-empty">该日无会话</li>
                    ) : sessions.map((s) => {
                      const tod = timeOfDay(s.started_at) || "会话";
                      const checked = value.days.includes(d.day) || value.session_ids.includes(s.session_id);
                      const active = activeSessionId === s.session_id;
                      return (
                        <li key={s.session_id} className={`scope-session${active ? " active" : ""}`}>
                          <label className="scope-check">
                            <input type="checkbox" aria-label={`投射 ${tod} ${s.name ?? ""}`.trim()}
                              checked={checked} onChange={() => toggleSession(s.session_id, d.day)} />
                            <span className="scope-session-time num">{tod}</span>
                            {s.name ? <span className="scope-session-name">{s.name}</span> : null}
                            <span className="scope-session-count num">{s.segment_count}段</span>
                          </label>
                          <button type="button" className="scope-session-inspect" aria-current={active ? "true" : undefined}
                            onClick={() => onInspectSession?.(s.session_id, d.day)}>{active ? "正在审核" : "审核身份"}</button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          });

  return (
    <section className="scope-selector card">
      <div className="scope-head">
        <div className="section-title" style={{ margin: 0 }}>
          <Icon name="inbox" /> 投射范围
        </div>
        <button type="button" className="ghost ghost-sm" onClick={clear} disabled={total === 0}>
          清空
        </button>
      </div>

      <p className="scope-hint muted">勾选日期或会话用于投射；点「审核身份」会把右侧闸机切到该会话。</p>
      {scopeError ? <p role="alert">{scopeError}</p> : null}

      {loading ? (
        <Skeleton label="正在载入日期" rows={4} />
      ) : days.length === 0 ? (
        <p className="muted">还没有任何日期。</p>
      ) : (
        <div className="scope-list">
          <div className="scope-period muted">最近 7 天</div>
          {recent.length ? <ul role="list">{renderDays(recent)}</ul> : <p className="muted">最近 7 天没有会话。</p>}
          {older.length ? <details className="scope-history" onToggle={(e) => setHistoryOpen(e.currentTarget.open)}><summary>更早日期 ({older.length})</summary>{historyOpen ? <ul role="list">{renderDays(older)}</ul> : null}</details> : null}
        </div>
      )}
    </section>
  );
}
