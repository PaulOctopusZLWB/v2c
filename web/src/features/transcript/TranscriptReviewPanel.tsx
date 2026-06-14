import type { Person, ReviewStatus, TranscriptSession } from "../../api/types";
import { reviewStatusZh, sessionHeader } from "../../lib/format";
import { Icon } from "../../components/Icon";
import { SegmentRow } from "./SegmentRow";

export function TranscriptReviewPanel({
  session,
  persons,
  highlightedSegmentId,
  evidenceSegmentIds,
  onReview,
  onOverride,
  onPlay
}: {
  session: TranscriptSession;
  persons: Person[];
  highlightedSegmentId?: string | null;
  evidenceSegmentIds?: Set<string>;
  onReview: (segmentId: string, status: ReviewStatus) => void;
  onOverride: (segmentId: string, personId: string) => void;
  onPlay: (segmentId: string) => void;
}) {
  const head = sessionHeader(session.segments);
  return (
    <section className="transcript-panel">
      <header className="panel-header">
        <h2>
          <Icon name="clock" /> 时段 {head.time} · {head.segs}段 · {head.speakers}人
        </h2>
        <span className={`badge s-${session.review_status}`}>{reviewStatusZh(session.review_status)}</span>
      </header>
      <p className="dim num session-id">{session.session_id}</p>
      <div className="segment-list">
        {session.segments.map((segment) => (
          <SegmentRow
            key={segment.segment_id}
            segment={segment}
            persons={persons}
            highlighted={highlightedSegmentId === segment.segment_id}
            isEvidence={evidenceSegmentIds?.has(segment.segment_id)}
            onReview={onReview}
            onOverride={onOverride}
            onPlay={onPlay}
          />
        ))}
      </div>
    </section>
  );
}
