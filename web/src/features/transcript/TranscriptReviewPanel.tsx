import type { Person, ReviewStatus, TranscriptSession } from "../../api/types";
import { reviewStatusZh, sessionHeader } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { groupIntoTurns } from "../../lib/turns";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";
import { TurnBlock } from "./TurnBlock";

export function TranscriptReviewPanel({
  session,
  persons,
  highlightedSegmentId,
  onBatchReview,
  onAcceptSession,
  onPlaybackError
}: {
  session: TranscriptSession;
  persons: Person[];
  highlightedSegmentId?: string | null;
  /** App owns the API call + refetch; the panel just hands up the ids + target status. */
  onBatchReview: (segment_ids: string[], status: ReviewStatus) => Promise<unknown> | void;
  /** Accept every remaining (un-reviewed) segment of the session in one shot. */
  onAcceptSession: () => Promise<unknown> | void;
  onPlaybackError?: (message: string) => void;
}) {
  const head = sessionHeader(session.segments);
  const turns = groupIntoTurns(session.segments);

  // Distinct speakers (in first-seen order) → all of that speaker's segment ids, for the
  // per-speaker "接受此人全部" control.
  const speakerSegmentIds = new Map<string, string[]>();
  for (const seg of session.segments) {
    const ids = speakerSegmentIds.get(seg.speaker) ?? [];
    ids.push(seg.segment_id);
    speakerSegmentIds.set(seg.speaker, ids);
  }

  const acceptSpeaker = useAsyncAction(async (ids: string[]) => { await onBatchReview(ids, "accepted"); });
  const acceptSession = useAsyncAction(async () => { await onAcceptSession(); });

  return (
    <section className="transcript-panel">
      <header className="panel-header">
        <h2>
          <Icon name="clock" /> 时段 {head.time} · {head.segs}段 · {head.speakers}人
        </h2>
        <span className={`badge s-${session.review_status}`}>{reviewStatusZh(session.review_status)}</span>
      </header>
      <p className="dim num session-id">{session.session_id}</p>

      <div className="session-actions">
        {Array.from(speakerSegmentIds.entries()).map(([speaker, ids]) => (
          <button
            key={speaker}
            className="chip-btn"
            style={{ borderColor: speakerColor(speaker) }}
            disabled={acceptSpeaker.pending}
            onClick={() => void acceptSpeaker.run(ids)}
          >
            <Icon name="accept" /> 接受此人全部 · {speaker}
          </button>
        ))}
        <button className="primary" disabled={acceptSession.pending} onClick={() => void acceptSession.run()}>
          <Icon name="check_circle" /> 接受整场
        </button>
      </div>

      <div className="turn-list">
        {turns.map((turn) => (
          <TurnBlock
            key={turn.segment_ids[0]}
            turn={turn}
            persons={persons}
            onBatchReview={onBatchReview}
            onPlaybackError={onPlaybackError}
            highlightedSegmentId={highlightedSegmentId ?? undefined}
          />
        ))}
      </div>
    </section>
  );
}
