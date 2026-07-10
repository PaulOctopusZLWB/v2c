import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { t } from "../../i18n";
import { dayLabel, reviewStatusZh, sessionListLabel } from "../../lib/format";
import type { DayStatusRow, ReviewStatus } from "../../api/types";
import { Icon } from "../../components/Icon";
import type { ConfirmFn, PromptFn } from "../../components/ui/Dialog";

type SessionRow = { session_id: string; started_at: string; segment_count: number; review_status: string; name?: string | null };

export function WorkspaceNav({
  days,
  dayStatus,
  selectedDay,
  selectedSessionId,
  sessionsVersion,
  onSelectDay,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
  confirm,
  promptText
}: {
  days: Array<{ day: string; session_count: number }>;
  dayStatus?: DayStatusRow[];
  selectedDay: string | null;
  selectedSessionId?: string | null;
  // Bumped by App after a rename/delete so this local session list refetches.
  sessionsVersion?: number;
  onSelectDay: (day: string) => void;
  onSelectSession: (sessionId: string) => void;
  // Owned by App (it holds the data refresh): rename/delete a session, then App re-fetches.
  onRenameSession?: (sessionId: string, name: string) => Promise<void> | void;
  onDeleteSession?: (sessionId: string) => Promise<void> | void;
  // App 的 Dialog API(替代 window.prompt/confirm)。
  confirm: ConfirmFn;
  promptText: PromptFn;
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
  }, [selectedDay, sessionsVersion]);

  return (
    <nav aria-label="日期与会话">
      <div className="section-title">
        <Icon name="inbox" /> {t.nav.days}
      </div>
      {days.map((d) => {
        const status = statusByDay.get(d.day);
        const statusClass = status === "ready" ? "s-accepted" : status === "empty" ? "s-rejected" : "s-pending_review";
        const statusLabel = status ? t.day[status] : "";
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
                <span className={`badge ${statusClass}`}>
                  {statusLabel}
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
            <div key={s.session_id} className={`session-row${s.session_id === selectedSessionId ? " selected" : ""}`}>
              <button
                type="button"
                aria-label={`${s.session_id} ${sessionListLabel(s)}`}
                className={`row-btn session-row-main${s.session_id === selectedSessionId ? " selected" : ""}`}
                onClick={() => onSelectSession(s.session_id)}
              >
                <span>{sessionListLabel(s)}</span>
                <span className={`badge s-${s.review_status}`}>
                  {reviewStatusZh(s.review_status as ReviewStatus | "blocked")}
                </span>
              </button>
              {onRenameSession ? (
                <button
                  type="button"
                  className="icon-btn session-row-action"
                  aria-label={`重命名「${sessionListLabel(s)}」`}
                  title="重命名"
                  onClick={() => {
                    void (async () => {
                      const next = await promptText({ title: "重命名会话", initial: s.name ?? "", placeholder: "会话名称" });
                      if (next !== null) void onRenameSession(s.session_id, next.trim());
                    })();
                  }}
                >
                  ✎
                </button>
              ) : null}
              {onDeleteSession ? (
                <button
                  type="button"
                  className="icon-btn session-row-action"
                  aria-label={`删除「${sessionListLabel(s)}」`}
                  title="删除"
                  onClick={() => {
                    void (async () => {
                      const ok = await confirm({
                        title: `删除会话「${sessionListLabel(s)}」?`,
                        body: <>该会话及其全部转写段将被移除,此操作<strong>不可撤销</strong>。</>,
                        confirmLabel: "删除"
                      });
                      if (ok) void onDeleteSession(s.session_id);
                    })();
                  }}
                >
                  🗑
                </button>
              ) : null}
            </div>
          ))}
        </>
      ) : null}
    </nav>
  );
}
