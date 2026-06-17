import { useEffect, useState } from "react";
import type { Person, ReviewStatus, TranscriptSession } from "../../api/types";
import { reviewStatusZh, sessionHeader } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { groupIntoTurns } from "../../lib/turns";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { useHotkeys } from "../command/useHotkeys";
import { Icon } from "../../components/Icon";
import { TurnBlock } from "./TurnBlock";
import { ShortcutSheet } from "./ShortcutSheet";

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

  // ── Keyboard-driven triage ───────────────────────────────────────────────
  // A focus ring moves between turns (j/k); a/r/f review the focused turn and advance;
  // space plays its first segment; ? toggles the shortcut sheet.
  const audio = useSegmentAudio();
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);

  // Reset focus to the top whenever the open session changes.
  useEffect(() => { setFocusedIdx(0); }, [session.session_id]);
  // Keep the focused index in range as the turn list grows/shrinks.
  useEffect(() => {
    setFocusedIdx((i) => Math.min(Math.max(i, 0), Math.max(turns.length - 1, 0)));
  }, [turns.length]);

  const lastIdx = Math.max(turns.length - 1, 0);
  const move = (delta: number) => setFocusedIdx((i) => Math.min(Math.max(i + delta, 0), lastIdx));
  const reviewFocused = (status: ReviewStatus) => {
    const turn = turns[focusedIdx];
    if (!turn) return;
    void onBatchReview(turn.segment_ids, status);
    move(1); // auto-advance after a decision
  };
  const playFocused = () => {
    const turn = turns[focusedIdx];
    const first = turn?.segments[0];
    if (!first) return;
    void audio
      .play(first.segment_id)
      .catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed"));
  };

  useHotkeys({
    j: () => move(1),
    arrowdown: (e) => { e.preventDefault(); move(1); },
    k: () => move(-1),
    arrowup: (e) => { e.preventDefault(); move(-1); },
    a: () => reviewFocused("accepted"),
    r: () => reviewFocused("rejected"),
    f: () => reviewFocused("needs_fix"),
    space: (e) => { e.preventDefault(); playFocused(); },
    // `?` reaches us as shift+/ or shift+? depending on the browser/layout; bind both.
    "shift+/": () => setHelpOpen((v) => !v),
    "shift+?": () => setHelpOpen((v) => !v),
    escape: () => setHelpOpen(false)
  });

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
        {turns.map((turn, i) => (
          <TurnBlock
            key={turn.segment_ids[0]}
            turn={turn}
            persons={persons}
            onBatchReview={onBatchReview}
            onPlaybackError={onPlaybackError}
            highlightedSegmentId={highlightedSegmentId ?? undefined}
            focused={i === focusedIdx}
          />
        ))}
      </div>

      <div className="review-hints num">
        <kbd>j</kbd>/<kbd>k</kbd> 移动 · <kbd>a</kbd> 接受 · <kbd>r</kbd> 拒绝 · <kbd>f</kbd> 存疑 ·{" "}
        <kbd>space</kbd> 播放 · <kbd>?</kbd> 帮助
      </div>

      {helpOpen ? <ShortcutSheet onClose={() => setHelpOpen(false)} /> : null}
    </section>
  );
}
