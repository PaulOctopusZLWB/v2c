import type { TranscriptSegment } from "../api/types";

/** A run of consecutive same-IDENTITY segments, reviewed as one batch but still made of
 *  individually-clickable sentences (each segment plays its own audio slice).
 *
 *  Identity is the *resolved* speaker: a person's `person_id` when attributed (so the diarizer's
 *  spk_NN no longer fragments one person), else the raw `speaker`. `label` is what the chip shows
 *  (the person's name when attributed, else the spk label); `personId` distinguishes the two. */
export interface Turn {
  /** Raw `speaker` of the turn's first segment (kept for color/back-compat). */
  speaker: string;
  /** Display label: `person_label` when attributed, else the raw spk label. */
  label: string;
  /** Resolved global person id, or null when unattributed. */
  personId: string | null;
  segments: TranscriptSegment[];
  segment_ids: string[];
  start: string | null;
  end: string | null;
}

/** The grouping key: the resolved identity (person when attributed, else raw spk). */
function identityOf(segment: TranscriptSegment): string {
  return segment.person_id ?? segment.speaker;
}

/** Single pass over the segments: start a new turn whenever the RESOLVED identity changes from
 *  the previous segment — so one person's consecutive segments merge even across different spk
 *  labels, and a spk label that flips attribution breaks. `start`/`end` are the absolute
 *  wall-clock bounds of the turn's first/last segment. Order is preserved. */
export function groupIntoTurns(segments: TranscriptSegment[]): Turn[] {
  const turns: Turn[] = [];
  let currentKey: string | null = null;
  for (const segment of segments) {
    const key = identityOf(segment);
    const current = turns[turns.length - 1];
    if (current && currentKey === key) {
      current.segments.push(segment);
      current.segment_ids.push(segment.segment_id);
      current.end = segment.absolute_end_at;
    } else {
      turns.push({
        speaker: segment.speaker,
        label: segment.person_label ?? segment.speaker,
        personId: segment.person_id,
        segments: [segment],
        segment_ids: [segment.segment_id],
        start: segment.absolute_start_at,
        end: segment.absolute_end_at
      });
      currentKey = key;
    }
  }
  return turns;
}
