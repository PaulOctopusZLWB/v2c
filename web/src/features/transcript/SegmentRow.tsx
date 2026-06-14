import { useState } from "react";
import type { Person, ReviewStatus, TranscriptSegment } from "../../api/types";
import { t } from "../../i18n";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";

const fmt = (ms: number) => {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

export function SegmentRow({
  segment, persons, highlighted, onReview, onOverride, onPlay
}: {
  segment: TranscriptSegment;
  persons: Person[];
  highlighted: boolean;
  onReview: (id: string, status: ReviewStatus) => Promise<unknown> | void;
  onOverride: (id: string, personId: string) => Promise<unknown> | void;
  onPlay: (id: string) => void;
}) {
  // Track which review status is in flight so only the clicked button shows "正在…".
  const [reviewing, setReviewing] = useState<ReviewStatus | null>(null);
  const review = useAsyncAction(async (id: string, status: ReviewStatus) => { await onReview(id, status); });
  const override = useAsyncAction(async (id: string, personId: string) => { await onOverride(id, personId); });
  const busy = review.pending || override.pending;

  const runReview = (status: ReviewStatus) => {
    setReviewing(status);
    void review.run(segment.segment_id, status).finally(() => setReviewing(null));
  };

  const reviewBtn = (status: ReviewStatus, label: string) => (
    <button
      onClick={() => runReview(status)}
      disabled={busy}
      aria-busy={review.pending && reviewing === status}
    >
      {review.pending && reviewing === status ? "正在…" : label}
    </button>
  );

  return (
    <article className={`segment-row${highlighted ? " hl" : ""}`} data-seg={segment.segment_id}>
      <span className="chip" style={{ background: speakerColor(segment.speaker) }}>{segment.speaker}</span>
      <time className="num">{fmt(segment.start_ms)}</time>
      <button aria-label="播放" title="播放" onClick={() => onPlay(segment.segment_id)}>▶</button>
      <p className="seg-text">{segment.text}</p>
      <span className={`status s-${segment.review_status}`}>{t.review[segment.review_status]}</span>
      <span className="actions">
        {reviewBtn("accepted", t.review.accepted)}
        {reviewBtn("rejected", t.review.rejected)}
        {reviewBtn("needs_fix", t.review.needs_fix)}
        <select aria-label={`${t.speaker.reassign} ${segment.segment_id}`} defaultValue="" disabled={busy}
          onChange={(e) => e.target.value && void override.run(segment.segment_id, e.target.value)}>
          <option value="" disabled>{t.speaker.reassign}…</option>
          {persons.map((p) => <option key={p.person_id} value={p.person_id}>{p.display_name}</option>)}
        </select>
      </span>
    </article>
  );
}
