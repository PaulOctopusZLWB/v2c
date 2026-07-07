import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { MemoryCandidate, MemoryCandidates } from "../../api/types";
import { Icon } from "../../components/Icon";
import { useHotkeys } from "../command/useHotkeys";
import { useSegmentAudio } from "../../hooks/useSegmentAudio";

/* 记忆确认页(design handoff 1e):候选卡(类型胶囊 + mono 来源·置信 + 证据行)
 * + 焦点卡展开操作行(a 确认并签名 / r 拒绝 / d 搁置)+ z 撤销上一操作。
 * 确认后卡片变 ok 描边并追加 mono 签名回执行;已签名的卡不可撤销(事件链 append-only)。 */

const TYPE_ZH: Record<string, { label: string; className: string }> = {
  preference: { label: "偏好", className: "is-preference" },
  fact: { label: "事实", className: "is-fact" },
  commitment: { label: "承诺", className: "is-commitment" },
  decision: { label: "决策", className: "is-commitment" },
  requirement: { label: "要求", className: "is-fact" },
  observation: { label: "观察", className: "is-fact" },
  todo: { label: "待办", className: "is-commitment" },
  relationship: { label: "关系", className: "is-preference" }
};

interface Receipt {
  card_id: string;
  signature: string;
  note_path: string | null;
}

export function MemoryPanel({
  push,
  onJumpToSegment,
  onPlaybackError
}: {
  push: (title: string, message?: string, variant?: "success" | "error") => void;
  /** 「跳到转写」:App 切到审核页并高亮该段。 */
  onJumpToSegment?: (segmentId: string, sessionId: string) => void;
  onPlaybackError?: (message: string) => void;
}) {
  const [data, setData] = useState<MemoryCandidates | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [receipts, setReceipts] = useState<Record<string, Receipt>>({});
  // z 撤销栈:最近的 reject/defer(confirm 已签名,不入栈)。
  const undoStack = useRef<Array<{ candidate_id: string; action: "rejected" | "deferred" }>>([]);
  const audio = useSegmentAudio();

  const load = useCallback(async () => {
    try {
      setData(await api.memoryCandidates());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const candidates = data?.candidates ?? [];
  // 页面语义:pending 前置(后端已排序);已处理的卡留在列表尾部供回看。
  const pendingCount = candidates.filter((c) => c.status === "pending_review").length;

  useEffect(() => {
    setFocusedIdx((i) => Math.min(Math.max(i, 0), Math.max(candidates.length - 1, 0)));
  }, [candidates.length]);

  const move = (delta: number) =>
    setFocusedIdx((i) => Math.min(Math.max(i + delta, 0), Math.max(candidates.length - 1, 0)));

  const act = async (candidate: MemoryCandidate, action: "confirm" | "reject" | "defer") => {
    if (candidate.status !== "pending_review") return;
    try {
      if (action === "confirm") {
        const receipt = await api.confirmMemory(candidate.candidate_id);
        setReceipts((prev) => ({
          ...prev,
          [candidate.candidate_id]: {
            card_id: receipt.card_id,
            signature: receipt.signature,
            note_path: receipt.note_path
          }
        }));
      } else if (action === "reject") {
        await api.rejectMemory(candidate.candidate_id);
        undoStack.current.push({ candidate_id: candidate.candidate_id, action: "rejected" });
      } else {
        await api.deferMemory(candidate.candidate_id);
        undoStack.current.push({ candidate_id: candidate.candidate_id, action: "deferred" });
      }
      await load();
      move(1);
    } catch (err) {
      push("记忆操作失败", err instanceof Error ? err.message : undefined);
    }
  };

  const undo = async () => {
    const last = undoStack.current.pop();
    if (!last) {
      push("没有可撤销的操作", "已签名的确认不可撤销(事件链只增不减)");
      return;
    }
    try {
      await api.restoreMemory(last.candidate_id);
      await load();
      push("已撤销", `候选已恢复为待确认`, "success");
    } catch (err) {
      push("撤销失败", err instanceof Error ? err.message : undefined);
    }
  };

  const actFocused = (action: "confirm" | "reject" | "defer") => {
    const candidate = candidates[focusedIdx];
    if (candidate) void act(candidate, action);
  };

  useHotkeys({
    j: () => move(1),
    arrowdown: (e) => { e.preventDefault(); move(1); },
    k: () => move(-1),
    arrowup: (e) => { e.preventDefault(); move(-1); },
    a: () => actFocused("confirm"),
    r: () => actFocused("reject"),
    d: () => actFocused("defer"),
    z: () => void undo()
  });

  const playEvidence = (segmentId: string) => {
    void audio.play(segmentId).catch((err) =>
      onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed")
    );
  };

  if (error) {
    return (
      <div className="tab-page single is-reading">
        <div className="empty error-state" role="alert">
          <Icon name="run" className="empty-icon" />
          <h3>记忆候选加载失败</h3>
          <p>{error}</p>
          <button className="primary" onClick={() => void load()}><Icon name="refresh" /> 重试</button>
        </div>
      </div>
    );
  }

  return (
    <div className="tab-page single is-reading memory-page">
      <header className="memory-head">
        <h2 className="memory-title">待确认记忆</h2>
        <span className="num dim">{pendingCount} / {data?.total ?? 0}</span>
        {data?.did ? (
          <span className="memory-did num" title={data.did}>
            🔑 Ed25519 · {data.did.length > 24 ? `${data.did.slice(0, 14)}…${data.did.slice(-4)}` : data.did}
          </span>
        ) : null}
      </header>

      {candidates.length === 0 && data ? (
        <div className="empty">
          <Icon name="check_circle" className="empty-icon" />
          <h3>没有待确认的记忆候选</h3>
          <p>每日总结生成后,新的记忆候选会出现在这里,确认即签名写入本地记忆库。</p>
        </div>
      ) : null}

      <div className="memory-list">
        {candidates.map((candidate, i) => {
          const type = TYPE_ZH[candidate.claim_type] ?? { label: candidate.claim_type, className: "is-fact" };
          const focused = i === focusedIdx;
          const pending = candidate.status === "pending_review";
          const receipt = receipts[candidate.candidate_id];
          return (
            <article
              key={candidate.candidate_id}
              className={`memory-card is-${candidate.status}${focused ? " focused" : ""}`}
              onClick={() => setFocusedIdx(i)}
            >
              <header className="memory-card-head">
                <span className={`memory-type ${type.className}`}>{type.label}</span>
                <span className="num dim">
                  {candidate.day ?? "—"} · 置信 {candidate.confidence != null ? candidate.confidence.toFixed(2) : "—"}
                </span>
                {candidate.status === "confirmed" ? <span className="memory-badge is-ok num">✓ 已确认</span> : null}
                {candidate.status === "rejected" ? <span className="memory-badge is-err num">✕ 已拒绝</span> : null}
                {candidate.status === "deferred" ? <span className="memory-badge is-warn num">◐ 已搁置</span> : null}
              </header>

              <p className="memory-claim">{candidate.claim}</p>

              {candidate.evidence.map((ev) => (
                <div className="memory-evidence" key={ev.evidence_id}>
                  <span className="memory-quote">「{ev.quote}」</span>
                  <span className="memory-evidence-end">
                    {ev.segment_id ? (
                      <>
                        <button
                          type="button"
                          className="memory-ev-btn"
                          aria-label={`播放证据 ${ev.evidence_id}`}
                          onClick={(e) => { e.stopPropagation(); playEvidence(ev.segment_id!); }}
                        >
                          ▶ 播放
                        </button>
                        {onJumpToSegment && ev.session_id ? (
                          <button
                            type="button"
                            className="memory-ev-btn"
                            onClick={(e) => { e.stopPropagation(); onJumpToSegment(ev.segment_id!, ev.session_id!); }}
                          >
                            跳到转写
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <span className="dim">{ev.source_type}</span>
                    )}
                  </span>
                </div>
              ))}

              {/* 确认后的 mono 签名回执行。 */}
              {candidate.status === "confirmed" ? (
                <p className="memory-receipt num">
                  ✓ memory_card.created
                  {receipt ? <> · sig {receipt.signature.slice(0, 4)}…{receipt.signature.slice(-4)}</> : null}
                  {candidate.memory_card_id ? <> · {candidate.memory_card_id.slice(0, 12)}…</> : null}
                  {" "}· 已写回 40_Confirmed_Memory/
                </p>
              ) : null}

              {focused && pending ? (
                <div className="memory-actions" onClick={(e) => e.stopPropagation()}>
                  <button className="memory-act is-confirm" onClick={() => void act(candidate, "confirm")}>
                    确认并签名 <kbd className="key-hint">a</kbd>
                  </button>
                  <button className="memory-act" onClick={() => void act(candidate, "reject")}>
                    拒绝 <kbd className="key-hint">r</kbd>
                  </button>
                  <button className="memory-act" onClick={() => void act(candidate, "defer")}>
                    搁置 <kbd className="key-hint">d</kbd>
                  </button>
                </div>
              ) : null}
            </article>
          );
        })}
      </div>

      <div className="review-hints num">
        <span><b>j/k</b> 移动</span> · <span><b>a</b> 确认签名</span> · <span><b>r</b> 拒绝</span> ·{" "}
        <span><b>d</b> 搁置</span> · <span><b>z</b> 撤销</span>
        <span className="review-hints-left">剩 {pendingCount} / {data?.total ?? 0}</span>
      </div>
    </div>
  );
}
