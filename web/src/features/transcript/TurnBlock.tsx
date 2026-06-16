import type { Person, ReviewStatus, TranscriptSegment } from "../../api/types";
import type { Turn } from "../../lib/turns";
import { clockOfDay } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { Icon } from "../../components/Icon";

/** A merged speaker turn rendered as one readable paragraph. The turn is the batch-review
 *  unit (accept/reject/flag the whole run), while every sentence inside stays clickable to
 *  play its own audio slice as evidence.
 *
 *  `persons` is part of the panel↔block contract (per-speaker reassignment lives in the panel)
 *  and is accepted here for parity even though this block renders no person picker. */
export function TurnBlock({
  turn,
  onBatchReview,
  onPlaybackError,
  highlightedSegmentId
}: {
  turn: Turn;
  persons: Person[];
  onBatchReview: (segment_ids: string[], status: ReviewStatus) => Promise<unknown> | void;
  onPlaybackError?: (message: string) => void;
  highlightedSegmentId?: string;
}) {
  const audio = useSegmentAudio();
  const review = useAsyncAction(async (status: ReviewStatus) => { await onBatchReview(turn.segment_ids, status); });

  const accepted = turn.segments.filter((s) => s.review_status === "accepted").length;

  const playSentence = (segment: TranscriptSegment) => {
    void audio
      .play(segment.segment_id)
      .catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed"));
  };

  const reviewBtn = (status: ReviewStatus, icon: string, label: string) => (
    <button onClick={() => void review.run(status)} disabled={review.pending}>
      <Icon name={icon} /> {label}
    </button>
  );

  return (
    <article className="turn" style={{ borderLeftColor: speakerColor(turn.speaker) }}>
      <header className="turn-head">
        <span className="chip" style={{ background: speakerColor(turn.speaker) }}>
          <Icon name="person" /> {turn.speaker}
        </span>
        <time className="num dim">
          {clockOfDay(turn.start)} – {clockOfDay(turn.end)}
        </time>
        <span className="turn-summary dim">{accepted}/{turn.segments.length} 已接受</span>
      </header>

      <p className="turn-text">
        {turn.segments.map((segment, i) => (
          <span key={segment.segment_id}>
            {i > 0 ? " " : null}
            <span
              className={`evi${audio.playing === segment.segment_id ? " playing" : ""}${
                highlightedSegmentId === segment.segment_id ? " hl" : ""
              }`}
              role="button"
              tabIndex={0}
              title={clockOfDay(segment.absolute_start_at)}
              onClick={() => playSentence(segment)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  playSentence(segment);
                }
              }}
            >
              {segment.text}
            </span>
          </span>
        ))}
      </p>

      <div className="turn-actions">
        {reviewBtn("accepted", "accept", "接受整段")}
        {reviewBtn("rejected", "reject", "拒绝整段")}
        {reviewBtn("needs_fix", "flag", "存疑")}
      </div>
    </article>
  );
}
