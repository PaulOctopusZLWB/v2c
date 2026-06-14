import type { Person, ReviewStatus, TranscriptSegment } from "../../api/types";
import { t } from "../../i18n";
import { speakerColor } from "../../lib/speakerColors";

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
  onReview: (id: string, status: ReviewStatus) => void;
  onOverride: (id: string, personId: string) => void;
  onPlay: (id: string) => void;
}) {
  return (
    <article className={`segment-row${highlighted ? " hl" : ""}`} data-seg={segment.segment_id}>
      <span className="chip" style={{ background: speakerColor(segment.speaker) }}>{segment.speaker}</span>
      <time className="num">{fmt(segment.start_ms)}</time>
      <button aria-label="播放" title="播放" onClick={() => onPlay(segment.segment_id)}>▶</button>
      <p className="seg-text">{segment.text}</p>
      <span className={`status s-${segment.review_status}`}>{t.review[segment.review_status]}</span>
      <span className="actions">
        <button onClick={() => onReview(segment.segment_id, "accepted")}>{t.review.accepted}</button>
        <button onClick={() => onReview(segment.segment_id, "rejected")}>{t.review.rejected}</button>
        <button onClick={() => onReview(segment.segment_id, "needs_fix")}>{t.review.needs_fix}</button>
        <select aria-label={`${t.speaker.reassign} ${segment.segment_id}`} defaultValue=""
          onChange={(e) => e.target.value && onOverride(segment.segment_id, e.target.value)}>
          <option value="" disabled>{t.speaker.reassign}…</option>
          {persons.map((p) => <option key={p.person_id} value={p.person_id}>{p.display_name}</option>)}
        </select>
      </span>
    </article>
  );
}
