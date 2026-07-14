import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { IdentityCandidate, IdentityReview, InboxSession, TranscriptSegment } from "../../api/types";
import { Icon } from "../../components/Icon";

// 收件箱 — 默认页。"每次开完会打开":最新的待定稿会话置顶,一张卡完成
// 出席确认(chips)→ 证据抽屉(按人分组的原文+试听)→ 定稿导出。
// 界面词汇只有人名和"声音A/B";机器标签(spk_ / vp_ 前缀)不出现在这里。

const HHMM = (value: string | null | undefined) => {
  const text = String(value ?? "");
  return text.length >= 16 ? text.slice(11, 16) : text;
};

type Push = (title: string, message?: string, tone?: "success" | "error") => void;

export function InboxPanel({
  push,
  onOpenWorkbench
}: {
  push: Push;
  /** "去人物工作台修" — 下钻到声纹地图/人物页,带上会话上下文。 */
  onOpenWorkbench?: (sessionId: string) => void;
}) {
  const [sessions, setSessions] = useState<InboxSession[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = async () => {
    const result = await api.inbox();
    const list = result?.sessions ?? [];
    setLoadError(null);
    setSessions(list);
    // 默认展开最新的待定稿会话(用户到这就是为了它)。
    setOpenId((current) => current ?? list.find((s) => !s.finalized)?.session_id ?? null);
  };

  useEffect(() => {
    // 初始加载失败走面板内联提示(整页 bootstrap 失败时已有全局 alert,别叠 toast)。
    void load().catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (loadError !== null && sessions === null) {
    return (
      <section className="card inbox-empty">
        <h2>收件箱加载失败</h2>
        <p className="dim">{loadError}</p>
      </section>
    );
  }
  if (sessions === null) {
    return (
      <section className="card inbox-empty">
        <h2>读取收件箱…</h2>
      </section>
    );
  }
  if (sessions.length === 0) {
    return (
      <section className="card inbox-empty">
        <h2>收件箱是空的</h2>
        <p>导入录音后,管道会自动转写、提取声纹并识别说话人;完成的会话会出现在这里等你确认出席。</p>
      </section>
    );
  }

  const pendingSessions = sessions.filter((s) => !s.finalized);
  const doneSessions = sessions.filter((s) => s.finalized);

  return (
    <div className="tab-page single inbox-layout">
      <section className="inbox-column">
        <header className="inbox-head">
          <h1>收件箱</h1>
          <span className="dim">
            {pendingSessions.length ? `${pendingSessions.length} 场待定稿` : "全部已定稿"}
          </span>
        </header>
        {pendingSessions.map((session) => (
          <InboxCard
            key={session.session_id}
            session={session}
            open={openId === session.session_id}
            onToggle={() => setOpenId(openId === session.session_id ? null : session.session_id)}
            onChanged={() => void load()}
            onOpenWorkbench={onOpenWorkbench}
            push={push}
          />
        ))}
        {doneSessions.length ? (
          <details className="inbox-done">
            <summary>已定稿 {doneSessions.length} 场</summary>
            {doneSessions.map((session) => (
              <InboxCard
                key={session.session_id}
                session={session}
                open={openId === session.session_id}
                onToggle={() => setOpenId(openId === session.session_id ? null : session.session_id)}
                onChanged={() => void load()}
                onOpenWorkbench={onOpenWorkbench}
                push={push}
              />
            ))}
          </details>
        ) : null}
      </section>
    </div>
  );
}

function InboxCard({
  session,
  open,
  onToggle,
  onChanged,
  onOpenWorkbench,
  push
}: {
  session: InboxSession;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onOpenWorkbench?: (sessionId: string) => void;
  push: Push;
}) {
  const [review, setReview] = useState<IdentityReview | null>(null);
  const [busy, setBusy] = useState(false);

  const loadReview = async () => {
    setReview(await api.identityReview(session.session_id));
  };

  useEffect(() => {
    if (open) void loadReview().catch((err) => push("身份状态读取失败", err instanceof Error ? err.message : undefined));
  }, [open, session.session_id]);

  const act = (fn: () => Promise<void>) => async () => {
    setBusy(true);
    try {
      await fn();
      await loadReview();
      onChanged();
    } catch (err) {
      push("操作失败", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const markParticipant = (candidate: IdentityCandidate, status: "present" | "absent") =>
    act(async () => {
      if (!candidate.person_id) return;
      const res = await api.setSessionParticipant(session.session_id, candidate.person_id, status);
      if (res.cascade?.cascade === "absent") {
        const cleared = res.cascade.cleared ?? 0;
        push(`已排除 ${candidate.display_name}`, cleared > 0 ? `清除 ${cleared} 段推断归属并重新识别` : undefined);
      }
    })();

  const notPerson = (candidate: IdentityCandidate) =>
    act(async () => {
      if (!candidate.person_id || candidate.segment_ids.length === 0) return;
      await api.notPerson({
        session_id: session.session_id,
        segment_ids: candidate.segment_ids,
        person_id: candidate.person_id
      });
    })();

  const finalize = act(async () => {
    const result = await api.finalizeSession(session.session_id);
    push("已定稿并导出", `${result.segment_count} 段 → ${result.export_md_path}`, "success");
  });

  const title = session.name || `会话 ${HHMM(session.started_at)} – ${HHMM(session.ended_at)}`;
  const canFinalize = review?.can_finalize ?? false;
  const activeCandidates = (review?.candidates ?? []).filter(
    (c) => c.status === "suggested" || c.status === "trusted"
  );

  return (
    <article className={`card inbox-card${session.finalized ? " is-finalized" : ""}`}>
      <button type="button" className="inbox-card-head" onClick={onToggle} aria-expanded={open}>
        <div className="inbox-card-title">
          <strong>{title}</strong>
          <span className="dim">
            {session.date_key} · {session.segment_count} 段
            {session.unidentified_count > 0 ? ` · ${session.unidentified_count} 段未识别` : ""}
          </span>
        </div>
        <div className="inbox-card-state">
          {session.present.length ? <span className="inbox-present">{session.present.join("、")}</span> : null}
          {session.finalized ? (
            <span className="inbox-badge done">已定稿</span>
          ) : (
            <span className="inbox-badge pending">待定稿</span>
          )}
        </div>
      </button>

      {open ? (
        <div className="inbox-card-body">
          {review === null ? (
            <p className="dim">读取身份状态…</p>
          ) : (
            <>
              <div className="inbox-attendance">
                {activeCandidates.map((candidate) => (
                  <div className="inbox-candidate" key={candidate.person_id ?? candidate.safe_label}>
                    <strong>{candidate.display_name ?? candidate.safe_label}</strong>
                    <span className="dim">{candidate.segment_count} 段</span>
                    <span className="inbox-candidate-actions">
                      <button type="button" disabled={busy} onClick={() => void markParticipant(candidate, "present")}>
                        出现了
                      </button>
                      <button type="button" disabled={busy} onClick={() => void markParticipant(candidate, "absent")}>
                        没出现
                      </button>
                      <button type="button" disabled={busy} onClick={() => void notPerson(candidate)}>
                        不是TA
                      </button>
                    </span>
                  </div>
                ))}
                {(review.new_person_candidates ?? []).map((candidate) => (
                  <div className="inbox-candidate unknown" key={candidate.safe_label}>
                    <strong>{candidate.safe_label}</strong>
                    <span className="dim">{candidate.segment_count} 段未识别</span>
                    {onOpenWorkbench ? (
                      <button type="button" onClick={() => onOpenWorkbench(session.session_id)}>
                        去认人
                      </button>
                    ) : null}
                  </div>
                ))}
                {activeCandidates.length === 0 && (review.new_person_candidates ?? []).length === 0 ? (
                  <p className="dim">没有待确认的声音。</p>
                ) : null}
              </div>

              <EvidenceDrawer sessionId={session.session_id} push={push} />

              <div className="inbox-card-actions">
                <button type="button" className="primary" disabled={busy || !canFinalize} onClick={() => void finalize()}>
                  {session.finalized ? "重新定稿并导出" : "定稿并导出"}
                </button>
                {!canFinalize ? <span className="dim">先确认至少一位出席者</span> : null}
                {review.finalized ? (
                  <span className="dim inbox-export-path">{review.finalized.export_md_path}</span>
                ) : null}
              </div>
            </>
          )}
        </div>
      ) : null}
    </article>
  );
}

/** 证据抽屉:按"人/声音"分组的原文,逐段可试听。逐字句不再是审核阶段,只是证据。 */
function EvidenceDrawer({
  sessionId,
  push
}: {
  sessionId: string;
  push: Push;
}) {
  const [groups, setGroups] = useState<Array<{ label: string; segments: TranscriptSegment[] }> | null>(null);
  const [openLabel, setOpenLabel] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = async () => {
    const session = await api.session(sessionId);
    // 人名优先;owner 声音显示为"我";其余未归属声音按出现顺序命名 声音A/B/C。
    const voiceLabels = new Map<string, string>();
    const labelOf = (segment: TranscriptSegment): string => {
      if (segment.person_label) return segment.person_label;
      if (segment.speaker === "self") return "我";
      const key = segment.speaker;
      if (!voiceLabels.has(key)) {
        const n = voiceLabels.size;
        voiceLabels.set(key, n < 26 ? `声音${String.fromCharCode(65 + n)}` : `声音${n + 1}`);
      }
      return voiceLabels.get(key)!;
    };
    const byLabel = new Map<string, TranscriptSegment[]>();
    for (const segment of session.segments) {
      const label = labelOf(segment);
      if (!byLabel.has(label)) byLabel.set(label, []);
      byLabel.get(label)!.push(segment);
    }
    setGroups(Array.from(byLabel.entries()).map(([label, segments]) => ({ label, segments })));
  };

  const play = (segmentId: string) => {
    if (audioRef.current) audioRef.current.pause();
    const audio = new Audio(api.audioUrl(segmentId));
    audioRef.current = audio;
    void audio.play().catch((err) => push("音频播放失败", err instanceof Error ? err.message : undefined));
  };

  useEffect(() => () => audioRef.current?.pause(), []);

  return (
    <details
      className="inbox-evidence"
      onToggle={(event) => {
        if ((event.target as HTMLDetailsElement).open && groups === null) {
          void load().catch((err) => push("原文读取失败", err instanceof Error ? err.message : undefined));
        }
      }}
    >
      <summary>
        <Icon name="mic" /> 证据:原文与录音
      </summary>
      {groups === null ? (
        <p className="dim">读取原文…</p>
      ) : (
        groups.map((group) => (
          <div className="inbox-evidence-group" key={group.label}>
            <button
              type="button"
              className="inbox-evidence-label"
              onClick={() => setOpenLabel(openLabel === group.label ? null : group.label)}
            >
              <strong>{group.label}</strong>
              <span className="dim">{group.segments.length} 段</span>
            </button>
            {openLabel === group.label ? (
              <ul className="inbox-evidence-list">
                {group.segments.map((segment) => (
                  <li key={segment.segment_id}>
                    <button type="button" className="ghost" onClick={() => play(segment.segment_id)} aria-label="播放">
                      ▶
                    </button>
                    <span className="dim">{HHMM(segment.absolute_start_at)}</span>
                    <span>{segment.text}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ))
      )}
    </details>
  );
}
