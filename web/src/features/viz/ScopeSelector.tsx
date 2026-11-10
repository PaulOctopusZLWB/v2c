import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { useDaysQuery } from "../../api/hooks";
import { dayLabel, timeOfDay } from "../../lib/format";
import { Icon } from "../../components/Icon";
import { Skeleton } from "../../components/ui";
import { groupSessions } from "../../lib/meetingGroups";
import type { MeetingGroup } from "../../api/types";

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
  member_session_ids?: string[];
  members?: SessionRow[];
};

function displaySessions(sessions: SessionRow[], groups: MeetingGroup[]): SessionRow[] {
  return groupSessions(sessions, groups).map(({ session, group, members }) => group ? {
    ...session, session_id: group.session_ids[0], started_at: group.started_at,
    name: group.title, segment_count: group.segment_count, member_session_ids: group.session_ids, members,
  } : session);
}

function memberIds(row: SessionRow): string[] { return row.member_session_ids ?? [row.session_id]; }

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
  onInspectSession?: (sessionId: string, day: string, followScope: boolean, scopeSessionIds?: string[]) => void;
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

  useEffect(() => {
    if (!activeSessionDay || !activeSessionId) return;
    setExpanded((previous) => previous.has(activeSessionDay) ? previous : new Set(previous).add(activeSessionDay));
    if (activeSessionDay < localDay(cutoff) || activeSessionDay > localDay(today)) setHistoryOpen(true);
    if (!sessionsByDay[activeSessionDay]) {
      api.sessionsForDay(activeSessionDay)
        .then((result) => setSessionsByDay((previous) => ({ ...previous, [activeSessionDay]: displaySessions(result.sessions ?? [], result.meeting_groups ?? []) })))
        .catch(() => setScopeError("日期中的会话读取失败，请重试。"));
    }
    // Reopening follows a new inspected session; it must not undo a manual collapse.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionDay, activeSessionId]);

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
          .then((r) => setSessionsByDay((m) => ({ ...m, [day]: displaySessions(r.sessions ?? [], r.meeting_groups ?? []) })))
          .catch(() => setScopeError("日期中的会话读取失败，请重试。"));
      }
      return next;
    });
  };

  const toggleDay = async (day: string, checked: boolean) => {
    setScopeError(null);
    const revision = clearRevision.current;
    let sessions = sessionsByDay[day];
    if (!sessions) {
      setLoadingDays((previous) => new Set(previous).add(day));
      try {
        const result = await api.sessionsForDay(day);
        sessions = displaySessions(result.sessions ?? [], result.meeting_groups ?? []);
        setSessionsByDay((previous) => ({ ...previous, [day]: sessions! }));
      } catch {
        setScopeError("日期中的会话读取失败，请重试。");
        return;
      } finally {
        setLoadingDays((previous) => { const next = new Set(previous); next.delete(day); return next; });
      }
    }
    if (revision !== clearRevision.current) return;
    const sessionIds = new Set(sessions.flatMap(memberIds));
    const current = valueRef.current;
    const next = {
      days: checked ? [...current.days.filter((d) => d !== day), day] : current.days.filter((d) => d !== day),
      session_ids: current.session_ids.filter((id) => !sessionIds.has(id))
    };
    valueRef.current = next;
    onChange(next);
    if (checked && sessions.length === 1 && sessions[0]) onInspectSession?.(sessions[0].session_id, day, false);
  };
  const toggleSession = (row: SessionRow, day: string) => {
    const sessions = sessionsByDay[day] ?? [];
    const current = valueRef.current;
    const daySelected = current.days.includes(day);
    const selected = new Set(current.session_ids);
    const ids = memberIds(row);
    if (daySelected) {
      for (const session of sessions) memberIds(session).forEach((id) => selected.add(id));
      ids.forEach((id) => selected.delete(id));
    } else if (ids.every((id) => selected.has(id))) ids.forEach((id) => selected.delete(id));
    else ids.forEach((id) => selected.add(id));
    const next = { days: current.days.filter((d) => d !== day), session_ids: [...selected] };
    valueRef.current = next;
    onChange(next);
    onInspectSession?.(row.session_id, day, false);
  };
  const clear = () => { clearRevision.current++; valueRef.current = { session_ids: [], days: [] }; onChange(valueRef.current); };

  const total = value.days.length + value.session_ids.length;

  const renderDays = (rows: typeof days) => rows.map((d) => {
            const open = expanded.has(d.day);
            const sessions = sessionsByDay[d.day];
            const countSelected = sessions?.flatMap(memberIds).filter((id) => value.session_ids.includes(id)).length ??
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
                      onChange={(e) => void toggleDay(d.day, e.target.checked)}
                    />
                    <span className="scope-day-label">{dayLabel(d.day)}</span>
                    <span className="scope-day-count num">{d.meeting_count ?? d.session_count}</span>
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
                      const ids = memberIds(s);
                      const checked = value.days.includes(d.day) || ids.every((id) => value.session_ids.includes(id));
                      const partiallyChecked = !checked && ids.some((id) => value.session_ids.includes(id));
                      const active = !!activeSessionId && ids.includes(activeSessionId);
                      return (
                        <li key={s.session_id} className={`scope-session${active ? " active" : ""}`}>
                          <label className="scope-check">
                            <input type="checkbox" aria-label={`投射 ${tod} ${s.name ?? ""}`.trim()}
                              checked={checked} ref={(element) => { if (element) element.indeterminate = partiallyChecked; }}
                              onChange={() => toggleSession(s, d.day)} />
                            <span className="scope-session-time num">{tod}</span>
                            {s.name ? <span className="scope-session-name">{s.name}</span> : null}
                            <span className="scope-session-count num">{s.segment_count}段</span>
                          </label>
                          <button type="button" className="scope-session-inspect" aria-current={active ? "true" : undefined}
                            onClick={() => onInspectSession?.(activeSessionId && active ? activeSessionId : s.session_id, d.day, true, ids)}>{active ? "正在审核" : "审核身份"}</button>
                          {s.members ? <div className="scope-session-parts" aria-label="原始会话">
                            {s.members.map((part) => <button key={part.session_id} type="button"
                              aria-label={`审核 ${timeOfDay(part.started_at)} 会话片段`}
                              aria-current={part.session_id === activeSessionId ? "true" : undefined}
                              onClick={() => onInspectSession?.(part.session_id, d.day, false)}>{timeOfDay(part.started_at)}</button>)}
                          </div> : null}
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
          {older.length ? <details className="scope-history" open={historyOpen} onToggle={(e) => setHistoryOpen(e.currentTarget.open)}><summary>更早日期 ({older.length})</summary>{historyOpen ? <ul role="list">{renderDays(older)}</ul> : null}</details> : null}
        </div>
      )}
    </section>
  );
}
