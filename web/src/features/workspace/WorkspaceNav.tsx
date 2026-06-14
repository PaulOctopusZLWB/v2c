import { useEffect, useState } from "react";
import { api } from "../../api/client";

export function WorkspaceNav({
  selectedDay,
  onSelectDay,
  onSelectSession
}: {
  selectedDay: string | null;
  onSelectDay: (day: string) => void;
  onSelectSession: (sessionId: string) => void;
}) {
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [sessions, setSessions] = useState<Array<{ session_id: string; review_status: string }>>([]);

  useEffect(() => {
    api.days().then((r) => setDays(r.days ?? [])).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!selectedDay) return;
    api.sessionsForDay(selectedDay).then((r) => setSessions(r.sessions ?? [])).catch(() => undefined);
  }, [selectedDay]);

  return (
    <nav aria-label="Days and sessions">
      <h3>Days</h3>
      {days.map((d) => (
        <button key={d.day} className={d.day === selectedDay ? "day active" : "day"} onClick={() => onSelectDay(d.day)}>
          {d.day} ({d.session_count})
        </button>
      ))}
      {selectedDay ? (
        <>
          <h3>Sessions</h3>
          {sessions.map((s) => (
            <button key={s.session_id} onClick={() => onSelectSession(s.session_id)}>
              {s.session_id} · {s.review_status}
            </button>
          ))}
        </>
      ) : null}
    </nav>
  );
}
