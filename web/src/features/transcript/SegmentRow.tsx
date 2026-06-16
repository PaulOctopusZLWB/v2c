import { useState } from "react";
import type { Person, ReviewStatus, TranscriptSegment } from "../../api/types";
import { t } from "../../i18n";
import { clock, clockOfDay, reviewStatusZh } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { Icon } from "../../components/Icon";

const BARS = 24;
/** A stable, quiet waveform silhouette per segment so the bar reads as a waveform
 *  at rest (real peaks replace it on play). Deterministic from the segment id. */
function restingWave(seed: string): number[] {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) h = (h ^ seed.charCodeAt(i)) * 16777619 >>> 0;
  return Array.from({ length: BARS }, (_, i) => {
    h = (h * 1103515245 + 12345) >>> 0;
    const taper = Math.sin((i / (BARS - 1)) * Math.PI); // fade in/out like a clip
    return 0.12 + ((h % 1000) / 1000) * 0.42 * (0.45 + 0.55 * taper);
  });
}

export function SegmentRow({
  segment, persons, highlighted, isEvidence, onReview, onOverride, onPlay, onPlaybackError
}: {
  segment: TranscriptSegment;
  persons: Person[];
  highlighted: boolean;
  isEvidence?: boolean;
  onReview: (id: string, status: ReviewStatus) => Promise<unknown> | void;
  onOverride: (id: string, personId: string) => Promise<unknown> | void;
  onPlay: (id: string) => void;
  onPlaybackError?: (message: string) => void;
}) {
  // Track which review status is in flight so only the clicked button shows its spinner.
  const [reviewing, setReviewing] = useState<ReviewStatus | null>(null);
  const [peaks, setPeaks] = useState<number[]>(() => restingWave(segment.segment_id));
  const review = useAsyncAction(async (id: string, status: ReviewStatus) => { await onReview(id, status); });
  const override = useAsyncAction(async (id: string, personId: string) => { await onOverride(id, personId); });
  const audio = useSegmentAudio();
  const busy = review.pending || override.pending;
  const playing = audio.playing === segment.segment_id;

  const runReview = (status: ReviewStatus) => {
    setReviewing(status);
    void review.run(segment.segment_id, status).finally(() => setReviewing(null));
  };

  const handlePlay = () => {
    onPlay(segment.segment_id);
    void audio
      .play(segment.segment_id)
      .then((p) => { if (p.length) setPeaks(normalize(p)); })
      .catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed"));
  };

  const reviewBtn = (status: ReviewStatus, icon: string, label: string) => {
    const active = review.pending && reviewing === status;
    return (
      <button onClick={() => runReview(status)} disabled={busy} aria-busy={active}>
        {active ? <span className="spinner" aria-hidden /> : <Icon name={icon} />}
        {label}
      </button>
    );
  };

  return (
    <article
      className={`segment-row${highlighted ? " hl" : ""}`}
      data-seg={segment.segment_id}
      style={{ borderLeftColor: speakerColor(segment.speaker) }}
    >
      <div className="seg-head">
        <span className="chip" style={{ background: speakerColor(segment.speaker) }}>
          <Icon name="person" /> {segment.speaker}
        </span>
        <time className="num dim">{clockOfDay(segment.absolute_start_at) || clock(segment.start_ms)}</time>
        <button className="icon-btn ghost" aria-label="播放" title="播放" onClick={handlePlay}>
          <Icon name="play" />
        </button>
        <span className={`wave${playing ? " playing" : ""}`} aria-hidden>
          {peaks.map((h, i) => (
            <i key={i} style={{ height: `${Math.max(2, Math.round(h * 22))}px` }} />
          ))}
        </span>
        {isEvidence ? (
          <span className="live" title={t.viewpoint.evidence}>
            <Icon name="viewpoint" />
          </span>
        ) : null}
        <span className="seg-head-end">
          <span className={`status s-${segment.review_status}`}>{reviewStatusZh(segment.review_status)}</span>
        </span>
      </div>

      <p className="seg-text">{segment.text}</p>

      <div className="actions">
        {reviewBtn("accepted", "accept", t.review.accepted)}
        {reviewBtn("rejected", "reject", t.review.rejected)}
        {reviewBtn("needs_fix", "flag", t.review.needs_fix)}
        <select
          aria-label={`${t.speaker.reassign} ${segment.segment_id}`}
          defaultValue=""
          disabled={busy}
          onChange={(e) => e.target.value && void override.run(segment.segment_id, e.target.value)}
        >
          <option value="" disabled>{t.speaker.reassign}…</option>
          {persons.map((p) => <option key={p.person_id} value={p.person_id}>{p.display_name}</option>)}
        </select>
      </div>
    </article>
  );
}

/** Scale peaks to 0..1 against the loudest bar so quiet clips still render. */
function normalize(values: number[]): number[] {
  const max = Math.max(...values, 0.0001);
  return values.map((v) => Math.min(1, v / max));
}
