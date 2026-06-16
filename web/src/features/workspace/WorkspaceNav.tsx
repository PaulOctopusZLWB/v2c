import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { t } from "../../i18n";
import { dayLabel, reviewStatusZh, sessionListLabel } from "../../lib/format";
import type { DayStatusRow, ReviewStatus } from "../../api/types";
import { Icon } from "../../components/Icon";

type SessionRow = { session_id: string; started_at: string; segment_count: number; review_status: string };

export function WorkspaceNav({
  days,
  dayStatus,
  selectedDay,
  selectedSessionId,
  onSelectDay,
  onSelectSession
}: {
  days: Array<{ day: string; session_count: number }>;
  dayStatus?: DayStatusRow[];
  selectedDay: string | null;
  selectedSessionId?: string | null;
  onSelectDay: (day: string) => void;
  onSelectSession: (sessionId: string) => void;
}) {
  const statusByDay = new Map((dayStatus ?? []).map((d) => [d.day, d.status]));
  // Days are owned by App (the top-level coordinator) so import/run refreshes flow here;
  // sessions stay local since they depend on the selected day.
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    if (!selectedDay) {
      setSessions([]);
      return;
    }
    api.sessionsForDay(selectedDay).then((r) => setSessions(r.sessions ?? [])).catch(() => undefined);
  }, [selectedDay]);

  return (
    <nav aria-label="日期与会话">
      <div className="section-title">
        <Icon name="inbox" /> {t.nav.days}
      </div>
      {days.map((d) => {
        const status = statusByDay.get(d.day);
        return (
          <button
            key={d.day}
            type="button"
            className={`row-btn${d.day === selectedDay ? " selected" : ""}`}
            onClick={() => onSelectDay(d.day)}
          >
            <span>{dayLabel(d.day)}</span>
            <span className="day-end">
              {status ? (
                <span className={`badge ${status === "ready" ? "s-accepted" : "s-pending_review"}`}>
                  {status === "ready" ? t.day.ready : t.day.processing}
                </span>
              ) : null}
              <span className="count">{d.session_count}</span>
            </span>
          </button>
        );
      })}

      {selectedDay ? (
        <>
          <div className="section-title">
            <Icon name="clock" /> {t.nav.sessions}
          </div>
          {sessions.map((s) => (
            <button
              key={s.session_id}
              type="button"
              aria-label={`${s.session_id} ${sessionListLabel(s)}`}
              className={`row-btn${s.session_id === selectedSessionId ? " selected" : ""}`}
              onClick={() => onSelectSession(s.session_id)}
            >
              <span>{sessionListLabel(s)}</span>
              <span className={`badge s-${s.review_status}`}>
                {reviewStatusZh(s.review_status as ReviewStatus | "blocked")}
              </span>
            </button>
          ))}
        </>
      ) : null}
    </nav>
  );
}
