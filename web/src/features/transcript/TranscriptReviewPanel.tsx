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
  onMatchCurrentSession,
  onPlaybackError
}: {
  session: TranscriptSession;
  persons: Person[];
  highlightedSegmentId?: string | null;
  /** App owns the API call + refetch; the panel just hands up the ids + target status. */
  onBatchReview: (segment_ids: string[], status: ReviewStatus) => Promise<unknown> | void;
  /** Accept every remaining (un-reviewed) segment of the session in one shot. */
  onAcceptSession: () => Promise<unknown> | void;
  /** Match this session's segments against the enrolled voiceprint library, then let App refetch. */
  onMatchCurrentSession?: () => Promise<unknown> | void;
  onPlaybackError?: (message: string) => void;
}) {
  const head = sessionHeader(session.segments);

  // ── Noise filters ────────────────────────────────────────────────────────
  // Two additive toggles recompute the visible turn list (without losing any data):
  //  · 隐藏碎语: drop turns whose segments are ALL ≤2 chars (the "呃/啊" fillers, ~1400 of them).
  //  · 仅未审:   drop turns that are already fully reviewed (every segment has a decision).
  const [hideFiller, setHideFiller] = useState(false);
  const [onlyPending, setOnlyPending] = useState(false);
  const allTurns = groupIntoTurns(session.segments);
  const turns = allTurns.filter((turn) => {
    if (hideFiller && turn.segments.every((s) => s.text.trim().length <= 2)) return false;
    if (onlyPending && turn.segments.every((s) => s.review_status !== "pending_review")) return false;
    return true;
  });

  // Distinct resolved identities (in first-seen order) → all matching segment ids, for the
  // per-identity "接受全部" controls. Voiceprint-attributed speakers collapse under the person.
  const identityActions = new Map<
    string,
    { key: string; label: string; personId: string | null; speakerLabels: string[]; segmentIds: string[] }
  >();
  for (const seg of session.segments) {
    const key = seg.person_id ?? seg.speaker;
    const existing = identityActions.get(key);
    if (existing) {
      existing.segmentIds.push(seg.segment_id);
      if (!existing.speakerLabels.includes(seg.speaker)) existing.speakerLabels.push(seg.speaker);
    } else {
      identityActions.set(key, {
        key,
        label: seg.person_label ?? seg.speaker,
        personId: seg.person_id,
        speakerLabels: [seg.speaker],
        segmentIds: [seg.segment_id]
      });
    }
  }

  const acceptSpeaker = useAsyncAction(async (ids: string[]) => { await onBatchReview(ids, "accepted"); });
  const acceptSession = useAsyncAction(async () => { await onAcceptSession(); });
  const matchCurrentSession = useAsyncAction(async () => { await onMatchCurrentSession?.(); });

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
    // When a filter (仅未审 / 隐藏碎语) will drop the just-reviewed turn from the
    // visible list, the optimistic patch shifts the list so the NEXT turn already
    // slides under `focusedIdx` — advancing again would skip it. Only auto-advance
    // when the reviewed turn stays in the list. The range clamp effect handles the
    // tail (reviewing the last visible turn).
    const willLeaveList =
      (onlyPending && status !== "pending_review") ||
      (hideFiller && turn.segments.every((s) => s.text.trim().length <= 2));
    if (!willLeaveList) move(1); // auto-advance after a decision
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
    // 仅在帮助面板真的打开时消费 Esc(preventDefault),否则让给上层
    // (App 的 Esc 关 toast);useHotkeys 会跳过已消费的键。
    escape: (e) => {
      if (!helpOpen) return;
      e.preventDefault();
      setHelpOpen(false);
    }
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
        {onMatchCurrentSession ? (
          <button
            type="button"
            className="ghost"
            disabled={matchCurrentSession.pending}
            onClick={() => void matchCurrentSession.run()}
          >
            {matchCurrentSession.pending ? <span className="spinner" aria-hidden /> : <Icon name="refresh" />}
            匹配当前会话
          </button>
        ) : null}
        {Array.from(identityActions.values()).map((group) => {
          const attributed = group.personId !== null;
          const rawSpeakers = group.speakerLabels.join(", ");
          return (
            <button
              key={group.key}
              className={`chip-btn${attributed ? " attributed" : ""}`}
              style={{ borderColor: speakerColor(group.key) }}
              disabled={acceptSpeaker.pending}
              title={attributed ? rawSpeakers : undefined}
              onClick={() => void acceptSpeaker.run(group.segmentIds)}
            >
              <Icon name="accept" />
              {attributed ? (
                <>
                  接受 <strong>{group.label}</strong> 全部 <span className="speaker-source num">{rawSpeakers}</span>
                </>
              ) : (
                <>接受此人全部 · {group.label}</>
              )}
            </button>
          );
        })}
        <button className="primary" disabled={acceptSession.pending} onClick={() => void acceptSession.run()}>
          <Icon name="check_circle" /> 接受整场
        </button>
      </div>

      <div className="review-filters">
        <label className="rf-toggle">
          <input type="checkbox" checked={hideFiller} onChange={(e) => setHideFiller(e.target.checked)} />
          <span>隐藏碎语 (≤2字)</span>
        </label>
        <label className="rf-toggle">
          <input type="checkbox" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} />
          <span>仅未审</span>
        </label>
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
        <button
          type="button"
          className={`level-toggle${audio.leveling ? " on" : ""}`}
          aria-pressed={audio.leveling}
          title="音量均衡:把每段语音归一到可听音量(应对有人声音大、有人声音小)"
          onClick={() => audio.setLeveling(!audio.leveling)}
        >
          <Icon name="volume" /> 音量均衡 {audio.leveling ? "开" : "关"}
        </button>
      </div>

      {helpOpen ? <ShortcutSheet onClose={() => setHelpOpen(false)} /> : null}
    </section>
  );
}
