import type { Person, ReviewStatus, TranscriptSegment } from "../../api/types";

export function SegmentRow({
  segment,
  persons,
  onReview,
  onOverride,
  onPlay
}: {
  segment: TranscriptSegment;
  persons: Person[];
  onReview: (segmentId: string, status: ReviewStatus) => void;
  onOverride: (segmentId: string, personId: string) => void;
  onPlay: (segmentId: string) => void;
}) {
  return (
    <article className="segment-row">
      <div>
        <button aria-label="Play segment" onClick={() => onPlay(segment.segment_id)}>Play</button>
        <span className="speaker-chip">{segment.speaker}</span>
        <time>{formatMs(segment.start_ms)}-{formatMs(segment.end_ms)}</time>
        <span>{segment.review_status}</span>
      </div>
      <p>{segment.text}</p>
      <div>
        <button onClick={() => onReview(segment.segment_id, "accepted")}>Accept</button>
        <button onClick={() => onReview(segment.segment_id, "rejected")}>Reject</button>
        <button onClick={() => onReview(segment.segment_id, "needs_fix")}>Flag</button>
        <select
          aria-label={`Override person for ${segment.segment_id}`}
          defaultValue=""
          onChange={(event) => event.target.value && onOverride(segment.segment_id, event.target.value)}
        >
          <option value="" disabled>Override person…</option>
          {persons.map((person) => (
            <option key={person.person_id} value={person.person_id}>{person.display_name}</option>
          ))}
        </select>
      </div>
    </article>
  );
}

function formatMs(value: number) {
  const seconds = Math.floor(value / 1000);
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}
