import type { Person, ReviewStatus, TranscriptSession } from "../../api/types";
import { SegmentRow } from "./SegmentRow";

export function TranscriptReviewPanel({
  session,
  persons,
  onReview,
  onOverride,
  onPlay
}: {
  session: TranscriptSession;
  persons: Person[];
  onReview: (segmentId: string, status: ReviewStatus) => void;
  onOverride: (segmentId: string, personId: string) => void;
  onPlay: (segmentId: string) => void;
}) {
  return (
    <section>
      <header className="panel-header">
        <h2>{session.session_id}</h2>
        <span>{session.review_status}</span>
      </header>
      <div className="segment-list">
        {session.segments.map((segment) => (
          <SegmentRow
            key={segment.segment_id}
            segment={segment}
            persons={persons}
            onReview={onReview}
            onOverride={onOverride}
            onPlay={onPlay}
          />
        ))}
      </div>
    </section>
  );
}
