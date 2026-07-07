import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { IdentityCandidate, IdentityReview } from "../../api/types";
import { Icon } from "../../components/Icon";

const STATUS_ZH: Record<string, string> = {
  trusted: "已信任",
  suggested: "待确认",
  excluded: "已排除",
  unknown: "未知",
  noise: "噪音",
  present: "出现",
  absent: "未出现",
  uncertain: "不确定"
};

export function IdentityReviewPanel({
  sessionId,
  onChanged,
  onReviewChange,
  onOpenClusters,
  onOpenSummary,
  push
}: {
  sessionId?: string | null;
  onChanged: () => void;
  onReviewChange?: (review: IdentityReview | null) => void;
  onOpenClusters?: () => void;
  onOpenSummary?: () => void;
  push: (title: string, message?: string) => void;
}) {
  const [review, setReview] = useState<IdentityReview | null>(null);
  const [busy, setBusy] = useState(false);

  const publishReview = (next: IdentityReview | null) => {
    setReview(next);
    onReviewChange?.(next);
  };

  const load = async () => {
    if (!sessionId) {
      publishReview(null);
      return;
    }
    publishReview(await api.identityReview(sessionId));
  };

  useEffect(() => {
    void load();
  }, [sessionId]);

  const markParticipant = async (candidate: IdentityCandidate, status: "present" | "absent" | "uncertain") => {
    if (!sessionId || !candidate.person_id) return;
    setBusy(true);
    try {
      await api.setSessionParticipant(sessionId, candidate.person_id, status);
      await load();
      onChanged();
    } catch (err) {
      push("身份标记失败", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const notPerson = async (candidate: IdentityCandidate) => {
    if (!sessionId || !candidate.person_id || candidate.segment_ids.length === 0) return;
    setBusy(true);
    try {
      await api.notPerson({ session_id: sessionId, segment_ids: candidate.segment_ids, person_id: candidate.person_id });
      await load();
      onChanged();
    } catch (err) {
      push("负反馈失败", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!sessionId) {
    return (
      <section className="card identity-review identity-review-empty">
        <div className="identity-gate-head">
          <span className="identity-kicker">身份闸机</span>
          <h2>先选一个会话</h2>
          <p>在左侧展开日期，点击会话行的「审核身份」。选中后这里会显示本场参与人、候选归属和“不是 TA”的负反馈入口。</p>
          {onOpenClusters ? (
            <button type="button" className="ghost identity-inline-action" onClick={onOpenClusters}>
              先看候选簇
            </button>
          ) : null}
        </div>
      </section>
    );
  }
  if (!review) {
    return (
      <section className="card identity-review">
        <div className="identity-gate-head">
          <span className="identity-kicker">身份闸机</span>
          <h2>读取身份状态…</h2>
        </div>
      </section>
    );
  }

  const present = review.participants.filter((p) => p.status === "present");
  const absent = review.participants.filter((p) => p.status === "absent");
  const activeCandidates = review.candidates.filter((candidate) => candidate.status === "suggested" || candidate.status === "unknown");
  const excludedCandidates = review.candidates.filter((candidate) => candidate.status === "excluded");
  const gateText = review.can_summarize ? "总结已放行" : "总结未放行";
  const nextStep = nextStepForReview({
    canSummarize: review.can_summarize,
    activeCandidateCount: activeCandidates.length,
    presentCount: present.length,
    absentCount: absent.length,
    excludedCount: excludedCandidates.length
  });

  return (
    <section className={`card identity-review ${review.can_summarize ? "is-open" : "is-blocked"}`}>
      <div className="identity-gate-head">
        <span className="identity-kicker">身份闸机</span>
        <h2>确认本场出现的人</h2>
        <p>只有这里标记为“出现”的人物姓名会进入总结。其他归属会在 LLM 前替换成未确认说话人。</p>
        <div className="identity-gate-status" aria-label="身份总结状态">
          <span className="identity-signal" aria-hidden>
            <Icon name="mic" />
          </span>
          <strong>{gateText}</strong>
          <span>{present.length} 位已确认 · {review.negative_feedback_count} 条负反馈</span>
        </div>
      </div>

      <div className="identity-next-step">
        <span>下一步</span>
        <strong>{nextStep.title}</strong>
        <p>{nextStep.detail}</p>
        {nextStep.action === "summary" && onOpenSummary ? (
          <button type="button" className="primary identity-inline-action" onClick={onOpenSummary}>
            去总结
          </button>
        ) : nextStep.action === "clusters" && onOpenClusters ? (
          <button type="button" className="ghost identity-inline-action" onClick={onOpenClusters}>
            打开候选簇
          </button>
        ) : null}
      </div>

      <div className="identity-section identity-participants">
        <div className="identity-section-title">
          <h3>本场参与人</h3>
          <span>{review.participants.length ? `${review.participants.length} 条确认记录` : "尚未确认"}</span>
        </div>
        {review.participants.length ? (
          <div className="identity-chip-row">
            {review.participants.map((p) => (
              <span className={`identity-chip s-${p.status}`} key={p.person_id}>
                <strong>{p.display_name}</strong>
                <span>{STATUS_ZH[p.status]}</span>
              </span>
            ))}
          </div>
        ) : (
          <p className="identity-empty">先从候选队列点击“出现了 X”。没有确认参与人时，总结按钮会保持禁用。</p>
        )}
      </div>

      <div className="identity-section">
        <div className="identity-section-title">
          <h3>候选队列</h3>
          <span>{activeCandidates.length} 个待确认</span>
        </div>
        {activeCandidates.length ? activeCandidates.map((candidate) => (
          <article className="identity-candidate" key={candidate.person_id ?? candidate.speaker ?? candidate.safe_label}>
            <div className="identity-candidate-head">
              <strong>{candidate.display_name ?? candidate.speaker ?? candidate.safe_label}</strong>
              <span className={`identity-status s-${candidate.status}`}>{STATUS_ZH[candidate.status] ?? candidate.status}</span>
            </div>
            <p className="identity-candidate-meta">{candidate.segment_count} 段证据 · 安全标签 {candidate.safe_label}</p>
            {candidate.sample_text ? <p className="identity-sample">{candidate.sample_text}</p> : null}
            {candidate.person_id ? (
              <div className="identity-actions">
                <button type="button" disabled={busy} onClick={() => void markParticipant(candidate, "present")}>
                  出现了 {candidate.display_name}
                </button>
                <button type="button" disabled={busy} onClick={() => void markParticipant(candidate, "absent")}>
                  本场没出现
                </button>
                <button type="button" disabled={busy} onClick={() => void notPerson(candidate)}>
                  不是 {candidate.display_name}
                </button>
              </div>
            ) : null}
          </article>
        )) : (
          <div className="identity-empty-block">
            <p className="identity-empty">没有待确认候选。被标为“本场没出现”的人已从本场白名单里排除，不会以真实姓名进入总结。</p>
            {onOpenClusters ? (
              <button type="button" className="ghost identity-inline-action" onClick={onOpenClusters}>
                打开候选簇
              </button>
            ) : null}
          </div>
        )}
        {excludedCandidates.length ? (
          <details className="identity-excluded">
            <summary>已排除 {excludedCandidates.length} 个候选</summary>
            <div className="identity-chip-row">
              {excludedCandidates.map((candidate) => (
                <span className="identity-chip s-absent" key={candidate.person_id ?? candidate.safe_label}>
                  <strong>{candidate.display_name ?? candidate.safe_label}</strong>
                  <span>不会进入总结姓名白名单</span>
                </span>
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </section>
  );
}

function nextStepForReview({
  canSummarize,
  activeCandidateCount,
  presentCount,
  absentCount,
  excludedCount
}: {
  canSummarize: boolean;
  activeCandidateCount: number;
  presentCount: number;
  absentCount: number;
  excludedCount: number;
}): { title: string; detail: string; action: "summary" | "clusters" | null } {
  if (activeCandidateCount > 0 && presentCount === 0) {
    return {
      title: "继续确认谁出现了",
      detail: `你已排除 ${absentCount + excludedCount} 个未出现候选。下一步从候选队列点“出现了 X”，建立本场白名单。`,
      action: null
    };
  }
  if (activeCandidateCount > 0) {
    return {
      title: `继续清理 ${activeCandidateCount} 个候选`,
      detail: "已经有出现者，剩下的候选只需要继续标“出现了”或“本场没出现”。未知说话人可以保留，不阻塞总结。",
      action: null
    };
  }
  if (canSummarize) {
    return {
      title: "身份足够，去总结",
      detail: "本场白名单已建立。已排除的人名会在 LLM 前替换为未确认说话人，不会污染总结。",
      action: "summary"
    };
  }
  return {
    title: "还缺一个出现者",
    detail: "你已经排除了候选，但本场还没有确认出现的人。去候选簇补一个归属，或在候选队列确认至少一位出现者。",
    action: "clusters"
  };
}
