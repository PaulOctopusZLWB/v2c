import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { t } from "../../i18n";
import { dayLabel, reviewStatusZh, sessionListLabel } from "../../lib/format";
import type { ReviewStatus } from "../../api/types";
import { Icon } from "../../components/Icon";

type SessionRow = { session_id: string; started_at: string; segment_count: number; review_status: string };

export function WorkspaceNav({
  selectedDay,
  selectedSessionId,
  onSelectDay,
  onSelectSession
}: {
  selectedDay: string | null;
  selectedSessionId?: string | null;
  onSelectDay: (day: string) => void;
  onSelectSession: (sessionId: string) => void;
}) {
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);

  useEffect(() => {
    api.days().then((r) => setDays(r.days ?? [])).catch(() => undefined);
  }, []);
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
      {days.map((d) => (
        <button
          key={d.day}
          type="button"
          className={`row-btn${d.day === selectedDay ? " selected" : ""}`}
          onClick={() => onSelectDay(d.day)}
        >
          <span>{dayLabel(d.day)}</span>
          <span className="count">{d.session_count}</span>
        </button>
      ))}

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
