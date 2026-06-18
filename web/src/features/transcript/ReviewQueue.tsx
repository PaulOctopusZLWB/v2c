import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { dayLabel, timeOfDay } from "../../lib/format";
import { Icon } from "../../components/Icon";
import type { ReviewQueueItem } from "../../api/types";

/**
 * The global "review inbox": one ranked queue of sessions that still need review, across every
 * day. Replaces the day -> session drill-down for the common case — the user just blows through
 * the top of the list. Refetches on mount and whenever `version` bumps (the parent bumps it after
 * a review action so a freshly-finished session leaves the list).
 */
export function ReviewQueue({
  activeSessionId,
  onOpen,
  version = 0
}: {
  activeSessionId: string | null;
  onOpen: (session_id: string, day: string) => void;
  /** Bump this from the parent after a review action to force a refetch. */
  version?: number;
}) {
  const [queue, setQueue] = useState<ReviewQueueItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let stale = false;
    api
      .reviewQueue()
      .then((r) => {
        if (stale) return;
        setQueue(r.queue ?? []);
        setLoaded(true);
      })
      .catch(() => {
        if (!stale) setLoaded(true);
      });
    return () => {
      stale = true;
    };
  }, [version]);

  // Empty state (only once a fetch has settled, so we don't flash "done" before the first load).
  if (loaded && queue.length === 0) {
    return (
      <nav className="review-queue" aria-label="待审队列">
        <div className="section-title">
          <Icon name="inbox" /> 待审队列
        </div>
        <div className="rq-done"><Icon name="check_circle" /> 全部已审完</div>
      </nav>
    );
  }

  return (
    <nav className="review-queue" aria-label="待审队列">
      <div className="section-title">
        <Icon name="inbox" /> 待审队列
      </div>
      {queue.map((item) => {
        const time = timeOfDay(item.started_at);
        return (
          <button
            key={item.session_id}
            type="button"
            className={`rq-item${item.session_id === activeSessionId ? " active" : ""}`}
            aria-label={`${dayLabel(item.day)} ${time} ${item.pending} 待审`}
            onClick={() => onOpen(item.session_id, item.day)}
          >
            <span className="rq-head">
              <span className="rq-when">
                {dayLabel(item.day)} · <span className="num">{time}</span>
              </span>
              {item.has_flag ? (
                <span className="rq-flag" title="存疑" aria-label="存疑">
                  ⚑
                </span>
              ) : null}
            </span>
            <span className="rq-meta">
              <span className="rq-count num">{item.pending} 待审</span>
              <span className="rq-speakers num">{item.speakers}人</span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
