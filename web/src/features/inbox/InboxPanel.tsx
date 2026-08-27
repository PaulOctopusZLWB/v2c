import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import type { IdentityCandidate, IdentityReview, InboxSession, Person, TranscriptSegment } from "../../api/types";
import { Icon } from "../../components/Icon";
import { speakerColor } from "../../lib/speakerColors";

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

const EVIDENCE_PAGE_SIZE = 40;
const EVIDENCE_MAX_SEGMENTS_PER_TURN = 8;

type EvidenceSegment = { label: string; segment: TranscriptSegment };
type EvidenceTurn = { label: string; segments: TranscriptSegment[] };

function markMatch(text: string, query: string): ReactNode {
  if (!query) return text;
  const lower = text.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let match = lower.indexOf(query);
  while (match >= 0) {
    if (match > cursor) parts.push(text.slice(cursor, match));
    parts.push(<mark key={`${match}-${parts.length}`}>{text.slice(match, match + query.length)}</mark>);
    cursor = match + query.length;
    match = lower.indexOf(query, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts.length ? parts : text;
}

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
  const [batchBusy, setBatchBusy] = useState(false);

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
  const finalizeReady = async () => {
    setBatchBusy(true);
    try {
      const result = await api.finalizeReadySessions();
      await load();
      push(
        result.finalized.length ? "已导出全部就绪会议" : "暂时没有可导出的会议",
        `${result.finalized.length} 场已导出，${result.skipped.length} 场仍需身份确认`,
        result.finalized.length ? "success" : undefined
      );
    } catch (err) {
      push("批量导出失败", err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBatchBusy(false);
    }
  };

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
          {pendingSessions.length ? (
            <button type="button" disabled={batchBusy} onClick={() => void finalizeReady()}>
              {batchBusy ? "正在逐场导出…" : "导出全部已就绪"}
            </button>
          ) : null}
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
  const [persons, setPersons] = useState<Person[]>([]);
  const [busy, setBusy] = useState(false);

  const loadReview = async () => {
    const [nextReview, nextPersons] = await Promise.all([
      api.identityReview(session.session_id),
      api.persons()
    ]);
    setReview(nextReview);
    setPersons(
      (nextPersons.persons ?? []).filter(
        (person) => person.person_type !== "non_speaker" && person.person_type !== "unknown_voice"
      )
    );
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

  const assignCandidate = (
    candidate: IdentityCandidate,
    action: "known_person" | "new_person" | "noise" | "unknown",
    options: { personId?: string; displayName?: string } = {}
  ) =>
    act(async () => {
      const result = await api.confirmIdentityCandidate({
        session_id: session.session_id,
        action,
        person_id: options.personId,
        display_name: options.displayName,
        segment_ids: candidate.segment_ids
      });
      const labels: Record<typeof action, string> = {
        known_person: "已分配给人物",
        new_person: "已创建人物并分配",
        noise: "已标记为噪音/多人",
        unknown: "已保留为未知声音"
      };
      push(labels[action], `${result.labeled} 段身份已固定`, "success");
    })();

  const title = sessionTitle(session);
  const canFinalize = review?.can_finalize ?? false;
  const activeCandidates = (review?.candidates ?? []).filter(
    (c) => c.eligible !== false && (c.status === "suggested" || c.status === "trusted")
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
                  <UnknownCandidateAssignment
                    key={candidate.safe_label}
                    candidate={candidate}
                    persons={persons}
                    busy={busy}
                    onAssign={(action, options) => void assignCandidate(candidate, action, options)}
                    onOpenWorkbench={onOpenWorkbench ? () => onOpenWorkbench(session.session_id) : undefined}
                  />
                ))}
                {(review.incidental_candidates ?? []).length ? (
                  <details className="inbox-incidental">
                    <summary>
                      已过滤 {review.incidental_candidates?.length} 个零散声音
                      <small>保留原文和音频，不参与人物候选，也不阻塞导出</small>
                    </summary>
                    <div>
                      {review.incidental_candidates?.map((candidate) => (
                        <span key={`${candidate.person_id ?? candidate.speaker}-${candidate.safe_label}`}>
                          {candidate.safe_label} · {candidate.segment_count} 段
                        </span>
                      ))}
                    </div>
                  </details>
                ) : null}
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
        {!canFinalize ? (
          <span className="dim">
            {(review?.gate?.present_count ?? 0) === 0
              ? "先确认至少一位出席者"
              : `还有 ${review?.gate?.unresolved_candidate_count ?? 0} 个有效声音需要分配角色`}
          </span>
        ) : null}
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

function UnknownCandidateAssignment({
  candidate,
  persons,
  busy,
  onAssign,
  onOpenWorkbench
}: {
  candidate: IdentityCandidate;
  persons: Person[];
  busy: boolean;
  onAssign: (
    action: "known_person" | "new_person" | "noise" | "unknown",
    options?: { personId?: string; displayName?: string }
  ) => void;
  onOpenWorkbench?: () => void;
}) {
  const [personId, setPersonId] = useState(persons[0]?.person_id ?? "");
  const [newName, setNewName] = useState("");

  useEffect(() => {
    if (!personId && persons[0]) setPersonId(persons[0].person_id);
  }, [persons, personId]);

  return (
    <div className="inbox-candidate unknown">
      <span className="inbox-candidate-name">
        <strong>{candidate.safe_label}</strong>
        <small>
          {candidate.segment_count} 段 · {Math.round((candidate.total_speech_ms ?? 0) / 1000)} 秒有效发言
        </small>
      </span>
      <div className="inbox-role-assignment">
        <span>
          <select aria-label={`把${candidate.safe_label}分配给人物`} value={personId} onChange={(event) => setPersonId(event.target.value)}>
            <option value="">选择已有人物</option>
            {persons.map((person) => <option key={person.person_id} value={person.person_id}>{person.display_name}</option>)}
          </select>
          <button type="button" disabled={busy || !personId} onClick={() => onAssign("known_person", { personId })}>分配</button>
        </span>
        <span>
          <input
            aria-label={`${candidate.safe_label}的新人物姓名`}
            value={newName}
            placeholder="新人物姓名"
            onChange={(event) => setNewName(event.target.value)}
          />
          <button type="button" disabled={busy || !newName.trim()} onClick={() => onAssign("new_person", { displayName: newName.trim() })}>
            创建并分配
          </button>
        </span>
        <span className="inbox-role-shortcuts">
          <button type="button" disabled={busy} onClick={() => onAssign("noise")}>噪音/多人</button>
          <button type="button" disabled={busy} onClick={() => onAssign("unknown")}>保留未知</button>
          {onOpenWorkbench ? <button type="button" disabled={busy} onClick={onOpenWorkbench}>去认人</button> : null}
        </span>
      </div>
    </div>
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
  const [segments, setSegments] = useState<EvidenceSegment[] | null>(null);
  const [query, setQuery] = useState("");
  const [speaker, setSpeaker] = useState("all");
  const [page, setPage] = useState(0);
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
    setSegments(session.segments.map((segment) => ({ label: labelOf(segment), segment })));
  };

  const speakerOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of segments ?? []) counts.set(item.label, (counts.get(item.label) ?? 0) + 1);
    return Array.from(counts, ([label, count]) => ({ label, count }));
  }, [segments]);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const turns = useMemo(() => {
    const all: EvidenceTurn[] = [];
    for (const item of segments ?? []) {
      const current = all[all.length - 1];
      if (current?.label === item.label && current.segments.length < EVIDENCE_MAX_SEGMENTS_PER_TURN) {
        current.segments.push(item.segment);
      }
      else all.push({ label: item.label, segments: [item.segment] });
    }
    return all.filter((turn) => {
      if (speaker !== "all" && turn.label !== speaker) return false;
      if (normalizedQuery && !turn.segments.some((segment) => segment.text.toLocaleLowerCase().includes(normalizedQuery))) return false;
      return true;
    });
  }, [segments, speaker, normalizedQuery]);

  const pageCount = Math.max(1, Math.ceil(turns.length / EVIDENCE_PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageStart = safePage * EVIDENCE_PAGE_SIZE;
  const visibleTurns = turns.slice(pageStart, pageStart + EVIDENCE_PAGE_SIZE);

  useEffect(() => setPage(0), [speaker, normalizedQuery]);

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
        if ((event.target as HTMLDetailsElement).open && segments === null) {
          void load().catch((err) => push("原文读取失败", err instanceof Error ? err.message : undefined));
        }
      }}
    >
      <summary>
        <Icon name="mic" /> 浏览原文与录音
      </summary>
      {segments === null ? (
        <p className="dim">读取原文…</p>
      ) : (
        <div className="inbox-evidence-browser">
          <div className="inbox-evidence-tools">
            <label className="inbox-evidence-search">
              <Icon name="search" />
              <span className="sr-only">搜索会话原文</span>
              <input
                type="search"
                value={query}
                placeholder="搜索这场会话…"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <span className="dim num">
              {turns.length ? `${pageStart + 1}–${Math.min(pageStart + EVIDENCE_PAGE_SIZE, turns.length)} / ${turns.length} 轮` : "0 轮"}
            </span>
          </div>

          <div className="inbox-evidence-speakers" aria-label="按说话人筛选原文">
            <button
              type="button"
              className={`inbox-evidence-speaker${speaker === "all" ? " active" : ""}`}
              aria-pressed={speaker === "all"}
              onClick={() => setSpeaker("all")}
            >
              全部 <span className="num">{segments.length}</span>
            </button>
            {speakerOptions.map((option) => (
              <button
                type="button"
                key={option.label}
                className={`inbox-evidence-speaker${speaker === option.label ? " active" : ""}`}
                aria-pressed={speaker === option.label}
                onClick={() => setSpeaker(option.label)}
              >
                <i style={{ background: speakerColor(option.label) }} aria-hidden />
                {option.label} <span className="num">{option.count}</span>
              </button>
            ))}
          </div>

          {pageCount > 1 ? (
            <div className="inbox-evidence-pages" aria-label="会话分段导航">
              <span className="num">时间轴</span>
              <div>
                {Array.from({ length: pageCount }, (_, index) => {
                  const first = turns[index * EVIDENCE_PAGE_SIZE];
                  return (
                    <button
                      type="button"
                      key={index}
                      className={index === safePage ? "active" : ""}
                      aria-label={`第 ${index + 1} 组，从 ${HHMM(first?.segments[0]?.absolute_start_at)} 开始`}
                      aria-pressed={index === safePage}
                      title={`${HHMM(first?.segments[0]?.absolute_start_at)} 开始`}
                      onClick={() => setPage(index)}
                    />
                  );
                })}
              </div>
            </div>
          ) : null}

          <div className="inbox-evidence-turns" aria-live="polite">
            {visibleTurns.map((turn) => (
              <article className="inbox-evidence-turn" key={turn.segments[0].segment_id}>
                <header>
                  <span className="inbox-evidence-person">
                    <i style={{ background: speakerColor(turn.label) }} aria-hidden />
                    <strong>{turn.label}</strong>
                  </span>
                  <span className="dim num">{HHMM(turn.segments[0].absolute_start_at)} · {turn.segments.length} 段</span>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => play(turn.segments[0].segment_id)}
                    aria-label={`播放 ${turn.label} ${HHMM(turn.segments[0].absolute_start_at)}`}
                  >
                    <Icon name="play" />
                  </button>
                </header>
                <p>
                  {turn.segments.map((segment, index) => (
                    <span key={segment.segment_id}>
                      {index ? " " : null}
                      <button
                        type="button"
                        className="inbox-evidence-sentence"
                        title={`${HHMM(segment.absolute_start_at)} · 点击播放`}
                        onClick={() => play(segment.segment_id)}
                      >
                        {markMatch(segment.text, normalizedQuery)}
                      </button>
                    </span>
                  ))}
                </p>
              </article>
            ))}
            {turns.length === 0 ? (
              <div className="inbox-evidence-none">
                没有符合当前条件的原文。
                <button type="button" onClick={() => { setQuery(""); setSpeaker("all"); }}>清除筛选</button>
              </div>
            ) : null}
          </div>

          {pageCount > 1 ? (
            <footer className="inbox-evidence-pagination">
              <button type="button" disabled={safePage === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一组</button>
              <span className="num">第 {safePage + 1} / {pageCount} 组</span>
              <button type="button" disabled={safePage >= pageCount - 1} onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}>下一组</button>
            </footer>
          ) : null}
        </div>
      )}
    </details>
  );
}
