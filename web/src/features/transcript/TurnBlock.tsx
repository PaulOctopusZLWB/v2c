import { useEffect, useRef } from "react";
import type { Person, ReviewStatus, TranscriptSegment, TriageReason } from "../../api/types";
import type { Turn } from "../../lib/turns";
import { clockOfDay } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { Icon } from "../../components/Icon";

/** turn 级审核状态:全部接受→accepted;有存疑→flagged;全部拒绝→rejected;
 *  还有待审段→pending;其余混合→mixed(已决策但不同类)。 */
export type TurnDecision = "pending" | "accepted" | "rejected" | "flagged" | "mixed";

export function turnDecision(turn: Turn): TurnDecision {
  const statuses = turn.segments.map((s) => s.review_status);
  if (statuses.some((s) => s === "pending_review")) return "pending";
  if (statuses.some((s) => s === "needs_fix")) return "flagged";
  if (statuses.every((s) => s === "accepted")) return "accepted";
  if (statuses.every((s) => s === "rejected")) return "rejected";
  return "mixed";
}

const DECIDED_BADGE: Record<Exclude<TurnDecision, "pending">, { label: string; className: string }> = {
  accepted: { label: "✓ 已接受", className: "is-accepted" },
  rejected: { label: "✕ 已拒绝", className: "is-rejected" },
  flagged: { label: "◐ 存疑", className: "is-flagged" },
  mixed: { label: "◐ 部分处理", className: "is-flagged" }
};

/** A merged speaker turn rendered as one review card(design handoff 1c). The turn is the
 *  batch-review unit; every sentence inside stays clickable to play its own audio slice.
 *  焦点卡展开操作行(a/e/r/f + mono 快捷键角标);已决策卡显示右上角状态角标并隐藏原因胶囊。
 *
 *  `persons` is part of the panel↔block contract (per-speaker reassignment lives in the panel)
 *  and is accepted here for parity even though this block renders no person picker. */
export function TurnBlock({
  turn,
  onBatchReview,
  onPlaybackError,
  highlightedSegmentId,
  focused,
  onFocus,
  reasons = [],
  suggestedSpeaker,
  onAdoptSpeaker
}: {
  turn: Turn;
  persons: Person[];
  onBatchReview: (segment_ids: string[], status: ReviewStatus) => Promise<unknown> | void;
  onPlaybackError?: (message: string) => void;
  highlightedSegmentId?: string;
  /** When true this turn carries the keyboard focus ring and is scrolled into view. */
  focused?: boolean;
  /** 点击卡片任意处把键盘焦点移到该卡(设计稿行为)。 */
  onFocus?: () => void;
  /** AI 预审的可疑原因胶囊(pending 时显示,最多 2 个)。 */
  reasons?: TriageReason[];
  /** AI 预审给出的更可能说话人;存在时出现「采纳 → XX」(e)。 */
  suggestedSpeaker?: { person_id: string; person_label: string } | null;
  /** 采纳建议说话人并接受本段(面板/ App 负责 override + accept + 刷新)。 */
  onAdoptSpeaker?: (segmentIds: string[], personId: string) => Promise<unknown> | void;
}) {
  const audio = useSegmentAudio();
  // Bring the turn into view whenever it gains the keyboard focus ring (j/k navigation).
  const articleRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focused) articleRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [focused]);
  const review = useAsyncAction(async (status: ReviewStatus) => { await onBatchReview(turn.segment_ids, status); });
  const adopt = useAsyncAction(async () => {
    if (suggestedSpeaker && onAdoptSpeaker) await onAdoptSpeaker(turn.segment_ids, suggestedSpeaker.person_id);
  });

  const decision = turnDecision(turn);
  const pending = decision === "pending";

  const playSentence = (segment: TranscriptSegment) => {
    void audio
      .play(segment.segment_id)
      .catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed"));
  };

  // Resolved identity drives both the colour and the chip label: a person when attributed
  // (turn.personId set), else the raw spk label rendered as "未识别".
  const color = speakerColor(turn.personId ?? turn.speaker);
  const attributed = turn.personId !== null;
  const durationS = ((turn.segments.reduce((ms, s) => ms + (s.end_ms - s.start_ms), 0)) / 1000).toFixed(1);

  return (
    <article
      ref={articleRef}
      className={`turn is-${decision}${focused ? " focused" : ""}`}
      onClick={onFocus}
    >
      <header className="turn-head">
        <span
          className={`chip${attributed ? "" : " unattributed"}`}
          style={{ background: color }}
          title={attributed ? undefined : "未识别说话人(尚未归属到人物)"}
        >
          <Icon name="person" /> {turn.label}
          {attributed ? null : <span className="unattributed-hint">未识别</span>}
        </span>
        <time className="num dim">
          {clockOfDay(turn.start)} · {durationS}s
        </time>
        {/* 可疑原因胶囊:决策后隐藏(设计稿),最多 2 个。 */}
        {pending
          ? reasons.slice(0, 2).map((reason) => (
              <span className="turn-reason" key={reason.kind + reason.label}>{reason.label}</span>
            ))
          : null}
        {!pending ? (
          <span className={`turn-badge num ${DECIDED_BADGE[decision].className}`}>
            {DECIDED_BADGE[decision].label}
          </span>
        ) : (
          <span className="turn-summary dim num">
            {turn.segments.filter((s) => s.review_status === "accepted").length}/{turn.segments.length}
          </span>
        )}
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
              onClick={(e) => { e.stopPropagation(); playSentence(segment); }}
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

      {/* 焦点卡的展开操作行(设计稿):a 接受 / e 采纳建议 / r 拒绝 / f 存疑。 */}
      {focused && pending ? (
        <div className="turn-actions" onClick={(e) => e.stopPropagation()}>
          <button className="turn-act is-accept" onClick={() => void review.run("accepted")} disabled={review.pending}>
            接受 <kbd className="key-hint">a</kbd>
          </button>
          {suggestedSpeaker && onAdoptSpeaker ? (
            <button className="turn-act is-adopt" onClick={() => void adopt.run()} disabled={adopt.pending}>
              采纳 → {suggestedSpeaker.person_label} <kbd className="key-hint">e</kbd>
            </button>
          ) : null}
          <button className="turn-act" onClick={() => void review.run("rejected")} disabled={review.pending}>
            拒绝 <kbd className="key-hint">r</kbd>
          </button>
          <button className="turn-act" onClick={() => void review.run("needs_fix")} disabled={review.pending}>
            存疑 <kbd className="key-hint">f</kbd>
          </button>
        </div>
      ) : null}
    </article>
  );
}
