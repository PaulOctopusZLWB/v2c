import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { IdentityCandidate, IdentityReview, InboxSession, TranscriptSegment } from "../../api/types";
import { Icon } from "../../components/Icon";

// 收件箱 — 默认页。左侧按录音时间轴浏览，右侧固定承载当前会话的
// 出席确认(chips)→ 证据抽屉(按人分组的原文+试听)→ 定稿导出。
// 界面词汇只有人名和"声音A/B";机器标签(spk_ / vp_ 前缀)不出现在这里。

const HHMM = (value: string | null | undefined) => {
  const text = String(value ?? "");
  return text.length >= 16 ? text.slice(11, 16) : text;
};

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

const dayLabel = (dateKey: string) => {
  const date = new Date(`${dateKey}T12:00:00`);
  const short = dateKey.slice(5).replace("-", ".");
  return `${short} ${Number.isNaN(date.getTime()) ? "" : WEEKDAYS[date.getDay()]}`.trim();
};

const durationLabel = (startedAt: string, endedAt: string) => {
  const durationMinutes = Math.max(
    0,
    Math.round((new Date(endedAt).getTime() - new Date(startedAt).getTime()) / 60_000)
  );
  if (durationMinutes < 60) return `${durationMinutes} 分钟`;
  const hours = Math.floor(durationMinutes / 60);
  const minutes = durationMinutes % 60;
  return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
};

const sessionTitle = (session: InboxSession) => session.name || `${HHMM(session.started_at)} 会话`;

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
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const load = async () => {
    const result = await api.inbox();
    const list = result?.sessions ?? [];
    setLoadError(null);
    setSessions(list);
    // 待处理优先；全部已定稿时仍选中最近会话，收件箱不会退化成一张空白页。
    setSelectedId((current) => {
      if (current && list.some((session) => session.session_id === current)) return current;
      return list.find((session) => !session.finalized)?.session_id ?? list[0]?.session_id ?? null;
    });
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
  const selectedSession = sessions.find((session) => session.session_id === selectedId) ?? sessions[0];

  return (
    <div className="tab-page inbox-layout">
      <header className="inbox-hero page-band">
        <div className="inbox-hero-copy">
          <span className="inbox-kicker num">MEETING INBOX · 会后处理</span>
          <h1>收件箱</h1>
          <p>确认谁出现，核对原声证据，再把会话定稿到本地知识库。</p>
        </div>
        <div className="inbox-totals" aria-label="收件箱统计">
          <span className={pendingSessions.length ? "is-pending" : "is-clear"}>
            <b className="num">{pendingSessions.length}</b><small>待处理</small>
          </span>
          <span>
            <b className="num">{sessions.length}</b><small>全部会话</small>
          </span>
        </div>
      </header>

      <div className="inbox-workspace page-body">
        <aside className="inbox-index" aria-label="会话时间轴">
          <header className="inbox-index-head">
            <div><span className="inbox-record-dot" aria-hidden /><strong>会话时间轴</strong></div>
            <span className="dim num">{sessions.length} RECORDS</span>
          </header>
          <div className="inbox-index-scroll panel-scroll">
            {pendingSessions.length ? (
              <SessionIndexSection
                label="待处理"
                sessions={pendingSessions}
                selectedId={selectedSession.session_id}
                onSelect={setSelectedId}
              />
            ) : (
              <p className="inbox-all-clear"><span aria-hidden>✓</span> 没有等待确认的会话</p>
            )}
            {doneSessions.length ? (
              <SessionIndexSection
                label="已定稿"
                sessions={doneSessions}
                selectedId={selectedSession.session_id}
                onSelect={setSelectedId}
              />
            ) : null}
          </div>
        </aside>

        <InboxSessionDetail
          key={selectedSession.session_id}
          session={selectedSession}
          onChanged={() => void load()}
          onOpenWorkbench={onOpenWorkbench}
          push={push}
        />
      </div>
    </div>
  );
}

function SessionIndexSection({
  label,
  sessions,
  selectedId,
  onSelect
}: {
  label: string;
  sessions: InboxSession[];
  selectedId: string;
  onSelect: (sessionId: string) => void;
}) {
  return (
    <section className="inbox-index-section" aria-label={`${label}会话`}>
      <div className="inbox-index-label"><span>{label}</span><span className="num">{sessions.length}</span></div>
      {sessions.map((session) => {
        const selected = session.session_id === selectedId;
        return (
          <button
            key={session.session_id}
            type="button"
            className={`inbox-session-row${selected ? " is-selected" : ""}`}
            aria-pressed={selected}
            onClick={() => onSelect(session.session_id)}
          >
            <span className="inbox-tape" aria-hidden><span /></span>
            <span className="inbox-session-copy">
              <span className="inbox-session-overline">
                <span>{dayLabel(session.date_key)}</span>
                <span className="num">{HHMM(session.started_at)}—{HHMM(session.ended_at)}</span>
              </span>
              <strong>{sessionTitle(session)}</strong>
              <span className="inbox-session-meta">
                <span>{session.segment_count.toLocaleString()} 段</span>
                {session.unidentified_count > 0 ? <span className="warn">{session.unidentified_count} 未识别</span> : <span>身份已收口</span>}
              </span>
            </span>
            <span className={`inbox-row-status ${session.finalized ? "done" : "pending"}`} aria-label={session.finalized ? "已定稿" : "待定稿"} />
          </button>
        );
      })}
    </section>
  );
}

function InboxSessionDetail({
  session,
  onChanged,
  onOpenWorkbench,
  push
}: {
  session: InboxSession;
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
    setReview(null);
    void loadReview().catch((err) => push("身份状态读取失败", err instanceof Error ? err.message : undefined));
  }, [session.session_id]);

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

  const title = sessionTitle(session);
  const canFinalize = review?.can_finalize ?? false;
  const activeCandidates = (review?.candidates ?? []).filter(
    (c) => c.status === "suggested" || c.status === "trusted"
  );
  const coverage = session.segment_count
    ? Math.round((session.attributed_count / session.segment_count) * 100)
    : 0;

  return (
    <article className={`inbox-detail${session.finalized ? " is-finalized" : ""}`} aria-label={`${title}详情`}>
      <header className="inbox-detail-head">
        <div className="inbox-detail-title">
          <span className="inbox-detail-overline num">{session.date_key} · {HHMM(session.started_at)}—{HHMM(session.ended_at)}</span>
          <h2>{title}</h2>
          <p>{durationLabel(session.started_at, session.ended_at)} · 本机录音会话</p>
        </div>
        <span className={`inbox-badge ${session.finalized ? "done" : "pending"}`}>
          {session.finalized ? "已定稿" : "待定稿"}
        </span>
      </header>

      <div className="inbox-facts" aria-label="会话概况">
        <span><small>时长</small><b className="num">{durationLabel(session.started_at, session.ended_at)}</b></span>
        <span><small>转写</small><b className="num">{session.segment_count.toLocaleString()} 段</b></span>
        <span><small>身份覆盖</small><b className="num">{coverage}%</b></span>
        <span className={session.unidentified_count ? "has-gap" : "is-complete"}>
          <small>未识别</small><b className="num">{session.unidentified_count.toLocaleString()} 段</b>
        </span>
      </div>

      <div className="inbox-detail-scroll panel-scroll">
        <section className="inbox-detail-section" aria-labelledby="inbox-people-title">
          <div className="inbox-section-head">
            <div><span className="inbox-section-index num">A</span><h3 id="inbox-people-title">出席与身份</h3></div>
            <span className="dim">{session.present.length} 位已确认</span>
          </div>
          {session.present.length ? (
            <div className="inbox-present-list">
              {session.present.map((person) => <span key={person}>{person}</span>)}
            </div>
          ) : null}

          {review === null ? (
            <p className="dim inbox-loading"><span className="spinner" aria-hidden /> 读取身份状态…</p>
          ) : (
            <div className="inbox-attendance">
                {activeCandidates.map((candidate) => (
                  <div className="inbox-candidate" key={candidate.person_id ?? candidate.safe_label}>
                    <span className="inbox-candidate-name"><strong>{candidate.display_name ?? candidate.safe_label}</strong><small>{candidate.segment_count} 段候选声音</small></span>
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
                    <span className="inbox-candidate-name"><strong>{candidate.safe_label}</strong><small>{candidate.segment_count} 段未识别</small></span>
                    {onOpenWorkbench ? (
                      <button type="button" onClick={() => onOpenWorkbench(session.session_id)}>
                        去认人
                      </button>
                    ) : null}
                  </div>
                ))}
                {activeCandidates.length === 0 && (review.new_person_candidates ?? []).length === 0 ? (
                  <p className="inbox-identity-clear"><span aria-hidden>✓</span> 身份判断已收口</p>
                ) : null}
            </div>
          )}
        </section>

        <section className="inbox-detail-section" aria-labelledby="inbox-evidence-title">
          <div className="inbox-section-head">
            <div><span className="inbox-section-index num">B</span><h3 id="inbox-evidence-title">原声证据</h3></div>
            <span className="dim">按说话人展开</span>
          </div>
          <EvidenceDrawer sessionId={session.session_id} push={push} />
        </section>
      </div>

      <footer className="inbox-detail-actions">
        <button type="button" className="primary" disabled={busy || !canFinalize} onClick={() => void finalize()}>
          {session.finalized ? "重新定稿并导出" : "定稿并导出"}
        </button>
        {!canFinalize ? <span className="dim">先确认至少一位出席者</span> : null}
        {(review?.finalized?.export_md_path ?? session.finalized?.export_md_path) ? (
          <span className="inbox-export-path" title={review?.finalized?.export_md_path ?? session.finalized?.export_md_path}>
            <span className="ok">✓ 已写入本地</span>
            <code>{review?.finalized?.export_md_path ?? session.finalized?.export_md_path}</code>
          </span>
        ) : null}
      </footer>
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
