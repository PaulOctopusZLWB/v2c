import { useState } from "react";
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

/** Toggle a value in/out of a string array (set semantics). */
function toggle(list: string[], v: string): string[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

/**
 * 范围选择 — pick multiple days and/or sessions to project together (cross-session comparison),
 * fully decoupled from the 审核 selection. Each day is a checkbox row (selecting it adds the
 * whole day to `days`) with an expander that lazily lists that day's sessions, each its own
 * checkbox (adds the session id to `session_ids`). Day- vs session-level selection are
 * independent — both supported. Compact + scrollable.
 */
export function ScopeSelector({ value, onChange }: { value: Scope; onChange: (v: Scope) => void }) {
  const daysQuery = useDaysQuery();
  const days = daysQuery.data?.days ?? [];
  const loading = daysQuery.isLoading;
  // Which day rows are expanded, and their lazily-fetched sessions (cached after first open).
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sessionsByDay, setSessionsByDay] = useState<Record<string, SessionRow[]>>({});

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
          .catch(() => setSessionsByDay((m) => ({ ...m, [day]: [] })));
      }
      return next;
    });
  };

  const toggleDay = (day: string) => onChange({ ...value, days: toggle(value.days, day) });
  const toggleSession = (sid: string) =>
    onChange({ ...value, session_ids: toggle(value.session_ids, sid) });
  const clear = () => onChange({ session_ids: [], days: [] });

  const total = value.days.length + value.session_ids.length;

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

      <p className="scope-hint muted">勾选日期或会话(可跨多天/多会话对比),再点「投射」。</p>

      {loading ? (
        <Skeleton label="正在载入日期" rows={4} />
      ) : days.length === 0 ? (
        <p className="muted">还没有任何日期。</p>
      ) : (
        <ul className="scope-list" role="list">
          {days.map((d) => {
            const open = expanded.has(d.day);
            const dayChecked = value.days.includes(d.day);
            const sessions = sessionsByDay[d.day];
            return (
              <li key={d.day} className="scope-day">
                <div className="scope-day-row">
                  <label className="scope-check">
                    <input
                      type="checkbox"
                      aria-label={dayLabel(d.day)}
                      checked={dayChecked}
                      onChange={() => toggleDay(d.day)}
                    />
                    <span className="scope-day-label">{dayLabel(d.day)}</span>
                    <span className="scope-day-count num">{d.session_count}</span>
                  </label>
                  <button
                    type="button"
                    className={`scope-expander${open ? " open" : ""}`}
                    aria-label={`展开 ${d.day}`}
                    aria-expanded={open}
                    onClick={() => toggleExpand(d.day)}
                    title={open ? "收起会话" : "展开会话"}
                  >
                    <Icon name="chevron" />
                  </button>
                </div>

                {open ? (
                  <ul className="scope-sessions" role="list">
                    {sessions === undefined ? (
                      <li className="scope-session-loading muted">
                        <span className="spinner" aria-hidden /> 载入会话…
                      </li>
                    ) : sessions.length === 0 ? (
                      <li className="muted scope-session-empty">该日无会话</li>
                    ) : (
                      sessions.map((s) => {
                        const tod = timeOfDay(s.started_at) || "会话";
                        const checked = value.session_ids.includes(s.session_id);
                        return (
                          <li key={s.session_id} className="scope-session">
                            <label className="scope-check">
                              <input
                                type="checkbox"
                                aria-label={`${tod} ${s.name ?? ""}`.trim()}
                                checked={checked}
                                onChange={() => toggleSession(s.session_id)}
                              />
                              <span className="scope-session-time num">{tod}</span>
                              {s.name ? <span className="scope-session-name">{s.name}</span> : null}
                              <span className="scope-session-count num">{s.segment_count}段</span>
                            </label>
                          </li>
                        );
                      })
                    )}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
