import type { TranscriptSegment } from "../api/types";

/** A run of consecutive same-speaker segments, reviewed as one batch but still made of
 *  individually-clickable sentences (each segment plays its own audio slice). */
export interface Turn {
  speaker: string;
  segments: TranscriptSegment[];
  segment_ids: string[];
  start: string | null;
  end: string | null;
}

/** Single pass over the segments: start a new turn whenever the speaker changes from the
 *  previous segment. `start`/`end` are the absolute wall-clock bounds of the turn's first/last
 *  segment. Order is preserved. */
export function groupIntoTurns(segments: TranscriptSegment[]): Turn[] {
  const turns: Turn[] = [];
  for (const segment of segments) {
    const current = turns[turns.length - 1];
    if (current && current.speaker === segment.speaker) {
      current.segments.push(segment);
      current.segment_ids.push(segment.segment_id);
      current.end = segment.absolute_end_at;
    } else {
      turns.push({
        speaker: segment.speaker,
        segments: [segment],
        segment_ids: [segment.segment_id],
        start: segment.absolute_start_at,
        end: segment.absolute_end_at
      });
    }
  }
  return turns;
}
