import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { Person, ReviewStatus, SessionTriage, TranscriptSession, TriageSegment } from "../../api/types";
import type { PromptFn } from "../../components/ui/Dialog";
import { reviewStatusZh, sessionHeader } from "../../lib/format";
import { speakerColor } from "../../lib/speakerColors";
import { groupIntoTurns, type Turn } from "../../lib/turns";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";
import { useHotkeys } from "../command/useHotkeys";
import { Icon } from "../../components/Icon";
import { TurnBlock } from "./TurnBlock";
import { ShortcutSheet } from "./ShortcutSheet";

/* AI 预审审核页(design handoff 1c):triage 把段分箱为 high/suspect/manual —
 * 可疑 turn 前置、高置信 turn 折叠(⇧A 一键批量接受,走 App 现成的乐观更新+撤销),
 * 头部 已审 x/N + 160px 进度条,底部 mono 快捷键条。triage 拉取失败时整页
 * 退化为普通人工审(无横幅/折叠),审核功能不受影响。 */

type TurnBin = "suspect" | "manual" | "high";

/** turn 的分箱:任一段可疑 → suspect;全部高置信 → high;其余(含无 triage 数据)→ manual。 */
function turnBin(turn: Turn, byId: Map<string, TriageSegment>): TurnBin {
  let allHigh = turn.segments.length > 0;
  for (const seg of turn.segments) {
    const t = byId.get(seg.segment_id);
    if (t?.bin === "suspect") return "suspect";
    if (t?.bin !== "high") allHigh = false;
  }
  return allHigh ? "high" : "manual";
}

export function TranscriptReviewPanel({
  session,
  persons,
  highlightedSegmentId,
  onBatchReview,
  onAcceptSession,
  onMatchCurrentSession,
  onPlaybackError,
  onAdoptSpeaker,
  onRenameSession,
  promptText,
  onArchive
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
  /** 采纳 AI 建议说话人:override 归属 + 接受(App 负责 API 与刷新)。 */
  onAdoptSpeaker?: (segment_ids: string[], personId: string) => Promise<unknown> | void;
  /** 重命名会话(头部 ✎;App 负责 API 与列表刷新)。 */
  onRenameSession?: (name: string) => Promise<unknown> | void;
  /** App 的输入对话框(重命名用);缺省时 ✎ 不显示。 */
  promptText?: PromptFn;
  /** 「接受整场并归档」后的去处(App: 回今日)。 */
  onArchive?: () => void;
}) {
  const head = sessionHeader(session.segments);

  // ── AI 预审 triage(自取,容错:失败仅隐藏预审 UI)─────────────────────
  const [triage, setTriage] = useState<SessionTriage | null>(null);
  useEffect(() => {
    let stale = false;
    setTriage(null);
    api
      .sessionTriage(session.session_id)
      // 形状校验:老后端/异常响应(如 {})直接当「无预审」,页面退化为普通人工审。
      .then((t) => { if (!stale && t && Array.isArray(t.segments) && t.summary?.bins) setTriage(t); })
      .catch(() => undefined);
    return () => { stale = true; };
  }, [session.session_id]);
  const triageById = useMemo(
    () => new Map((triage?.segments ?? []).map((t) => [t.segment_id, t])),
    [triage]
  );

  // ── Noise filters ────────────────────────────────────────────────────────
  const [hideFiller, setHideFiller] = useState(false);
  const [onlyPending, setOnlyPending] = useState(false);
  // 折叠的高置信 turn 可随时展开复核。
  const [showHigh, setShowHigh] = useState(false);

  const allTurns = groupIntoTurns(session.segments);
  const binOf = useMemo(() => {
    const map = new Map<Turn, TurnBin>();
    for (const turn of allTurns) map.set(turn, triage ? turnBin(turn, triageById) : "manual");
    return map;
  }, [allTurns, triage, triageById]);

  const filtered = allTurns.filter((turn) => {
    if (hideFiller && turn.segments.every((s) => s.text.trim().length <= 2)) return false;
    if (onlyPending && turn.segments.every((s) => s.review_status !== "pending_review")) return false;
    return true;
  });
  const highTurns = filtered.filter((t) => binOf.get(t) === "high");
  // 可疑前置(组内保持时间序);高置信折叠,展开时排在最后。
  const turns = [
    ...filtered.filter((t) => binOf.get(t) === "suspect"),
    ...filtered.filter((t) => binOf.get(t) === "manual"),
    ...(showHigh ? highTurns : [])
  ];

  // ── Live 预审计数(跟随乐观更新的 review_status)─────────────────────────
  const pendingHighIds = session.segments
    .filter((s) => s.review_status === "pending_review" && triageById.get(s.segment_id)?.bin === "high")
    .map((s) => s.segment_id);
  const highTotal = triage?.summary.bins.high ?? 0;
  const suspLeft = allTurns.filter(
    (t) => binOf.get(t) === "suspect" && t.segments.some((s) => s.review_status === "pending_review")
  ).length;
  const reviewedCount = session.segments.filter((s) => s.review_status !== "pending_review").length;
  const totalCount = session.segments.length;
  const pendingCount = totalCount - reviewedCount;

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
  const acceptHigh = useAsyncAction(async () => {
    if (pendingHighIds.length) await onBatchReview(pendingHighIds, "accepted");
  });

  const rename = async () => {
    if (!promptText || !onRenameSession) return;
    const next = await promptText({ title: "重命名会话", initial: session.name ?? "", placeholder: "会话名称" });
    if (next !== null) void onRenameSession(next.trim());
  };

  // ── Keyboard-driven review ───────────────────────────────────────────────
  const audio = useSegmentAudio();
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);

  useEffect(() => { setFocusedIdx(0); }, [session.session_id]);
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
    // slides under `focusedIdx` — advancing again would skip it.
    const willLeaveList =
      (onlyPending && status !== "pending_review") ||
      (hideFiller && turn.segments.every((s) => s.text.trim().length <= 2));
    if (!willLeaveList) move(1); // auto-advance after a decision
  };
  const adoptFocused = () => {
    const turn = turns[focusedIdx];
    if (!turn || !onAdoptSpeaker) return;
    const suggestion = turn.segments
      .map((s) => triageById.get(s.segment_id)?.suggested_speaker)
      .find((s) => s != null);
    if (suggestion) void onAdoptSpeaker(turn.segment_ids, suggestion.person_id);
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
    e: () => adoptFocused(),
    r: () => reviewFocused("rejected"),
    f: () => reviewFocused("needs_fix"),
    "shift+a": () => { if (pendingHighIds.length) void acceptHigh.run(); },
    space: (e) => { e.preventDefault(); playFocused(); },
    // `?` reaches us as shift+/ or shift+? depending on the browser/layout; bind both.
    "shift+/": () => setHelpOpen((v) => !v),
    "shift+?": () => setHelpOpen((v) => !v),
    // 仅在帮助面板真的打开时消费 Esc(preventDefault),否则让给上层。
    escape: (e) => {
      if (!helpOpen) return;
      e.preventDefault();
      setHelpOpen(false);
    }
  });

  const suggestionOf = (turn: Turn) =>
    turn.segments.map((s) => triageById.get(s.segment_id)?.suggested_speaker).find((s) => s != null) ?? null;
  const reasonsOf = (turn: Turn) => {
    const seen = new Set<string>();
    const list = [];
    for (const seg of turn.segments) {
      for (const reason of triageById.get(seg.segment_id)?.reasons ?? []) {
        if (!seen.has(reason.label)) {
          seen.add(reason.label);
          list.push(reason);
        }
      }
    }
    return list;
  };

  return (
    <section className="transcript-panel">
      {/* 头部:会话名 + ✎ 重命名 + mono 元信息;右侧 已审 x/N + 160px 进度条。 */}
      <header className="review-head">
        <h2 className="review-title">{session.name?.trim() || `时段 ${head.time}`}</h2>
        {promptText && onRenameSession ? (
          <button type="button" className="review-rename" title="重命名" aria-label="重命名会话" onClick={() => void rename()}>
            ✎
          </button>
        ) : null}
        <span className="review-meta num dim">
          {head.time} · {head.segs} 段 · {head.speakers} 人
        </span>
        <span className={`badge s-${session.review_status}`}>{reviewStatusZh(session.review_status)}</span>
        <span className="review-progress">
          <span className="dim">已审</span>
          <span className="num">{reviewedCount}/{totalCount}</span>
          <span className="review-progress-track" role="progressbar" aria-label="审核进度" aria-valuenow={reviewedCount} aria-valuemin={0} aria-valuemax={totalCount}>
            <span
              className="review-progress-fill"
              style={{ width: totalCount > 0 ? `${Math.round((reviewedCount / totalCount) * 100)}%` : "0%" }}
            />
          </span>
        </span>
      </header>
      <p className="dim num session-id">{session.session_id}</p>

      {/* AI 预审横幅:高置信批量接受(⇧A)+ 可疑计数。 */}
      {triage && highTotal + suspLeft > 0 ? (
        <div className="triage-banner">
          <span className="breathe-dot" aria-hidden />
          <span className="triage-banner-copy">
            <strong>AI 预审完成。</strong>剩余段中 <b className="num ok">{pendingHighIds.length}</b> 段高置信建议直接接受;
            <b className="num warn">{suspLeft}</b> 段可疑已前置。
          </span>
          {pendingHighIds.length > 0 ? (
            <button type="button" className="primary" disabled={acceptHigh.pending} onClick={() => void acceptHigh.run()}>
              接受 {pendingHighIds.length} 段高置信 <kbd className="key-hint">⇧A</kbd>
            </button>
          ) : highTotal > 0 ? (
            <span className="triage-banner-done num">✓ 已接受 {highTotal} 段</span>
          ) : null}
        </div>
      ) : null}

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
            onFocus={() => setFocusedIdx(i)}
            reasons={reasonsOf(turn)}
            suggestedSpeaker={suggestionOf(turn)}
            onAdoptSpeaker={onAdoptSpeaker}
          />
        ))}

        {/* 完成态:无待审段时出 ok 卡。 */}
        {totalCount > 0 && pendingCount === 0 ? (
          <div className="review-complete">
            <span className="review-complete-check" aria-hidden>✓</span>
            <span className="review-complete-copy">本场审核完成 — 全部 {totalCount} 段已处理。</span>
            {onArchive ? (
              <button type="button" className="review-complete-btn" onClick={onArchive}>
                接受整场并归档
              </button>
            ) : null}
          </div>
        ) : null}

        {/* 高置信折叠行(虚线):点击展开/收起复核。 */}
        {triage && highTurns.length > 0 ? (
          <button type="button" className="triage-collapsed" onClick={() => setShowHigh((v) => !v)}>
            <span className="ok" aria-hidden>✓</span>
            其余 <b className="num">{highTurns.length}</b> 段高置信(≥{triage.thresholds.high.toFixed(2)})已{showHigh ? "展开" : "折叠"}
            <span className="triage-collapsed-toggle">{showHigh ? "收起 ▴" : "展开 ▾"}</span>
          </button>
        ) : null}
      </div>

      {/* 底部快捷键条(mono):j/k a e r f ⇧A + 剩 n 段可疑。 */}
      <div className="review-hints num">
        <span><b>j/k</b> 移动</span> · <span><b>a</b> 接受</span> · <span><b>e</b> 采纳建议</span> ·{" "}
        <span><b>r</b> 拒绝</span> · <span><b>f</b> 存疑</span> · <span><b>⇧A</b> 批量接受</span> ·{" "}
        <span><b>space</b> 播放</span> · <span><b>?</b> 帮助</span>
        <button
          type="button"
          className={`level-toggle${audio.leveling ? " on" : ""}`}
          aria-pressed={audio.leveling}
          title="音量均衡:把每段语音归一到可听音量(应对有人声音大、有人声音小)"
          onClick={() => audio.setLeveling(!audio.leveling)}
        >
          <Icon name="volume" /> 音量均衡 {audio.leveling ? "开" : "关"}
        </button>
        {triage ? <span className="review-hints-left">剩 {suspLeft} 段可疑</span> : null}
      </div>

      {helpOpen ? <ShortcutSheet onClose={() => setHelpOpen(false)} /> : null}
    </section>
  );
}
