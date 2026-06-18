import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Icon } from "../../components/Icon";
import { dayLabel, reviewStatusZh } from "../../lib/format";
import type { HomeOverview } from "../../api/types";

interface HomePanelProps {
  onGoReview: () => void;
  onGoSpeakers: () => void;
  onGoLlm: (day: string) => void;
  onOpenSession: (sessionId: string, day: string) => void;
}

/** Percentage of `part`/`whole` as a rounded integer (0 when there's nothing to cover). */
function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

/**
 * 首页 (home/landing): the app opens here, on value rather than machinery. Five actionable
 * cards — 待审 backlog, 人物 roster, 覆盖 corpus stats, 最近会话, and an 洞察 jump to the
 * latest day — each deep-linking into the relevant tab via the on* callbacks.
 */
export function HomePanel({ onGoReview, onGoSpeakers, onGoLlm, onOpenSession }: HomePanelProps) {
  const [overview, setOverview] = useState<HomeOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    api
      .homeOverview()
      .then((data) => { if (!stale) setOverview(data); })
      .catch((err) => { if (!stale) setError(err instanceof Error ? err.message : "加载失败"); });
    return () => { stale = true; };
  }, []);

  if (error) {
    return (
      <div className="tab-page single is-reading">
        <div className="empty error-state" role="alert">
          <Icon name="run" className="empty-icon" />
          <h3>首页加载失败</h3>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!overview) {
    return (
      <div className="tab-page single is-reading">
        <div className="empty">
          <Icon name="refresh" className="empty-icon" />
          <h3>正在加载概览…</h3>
        </div>
      </div>
    );
  }

  const { review, people, coverage, recent_sessions, latest_day } = overview;
  const noBacklog = review.pending_sessions === 0;

  return (
    <div className="tab-page single is-reading home">
      <div className="home-grid">
        {/* 待审 — the single most actionable number; the whole card jumps to 审核. */}
        <button type="button" aria-label="待审" className={`home-card home-card-review${noBacklog ? " is-clear" : ""}`} onClick={onGoReview}>
          <div className="home-card-head">
            <span className="home-card-label"><Icon name="inbox" /> 待审</span>
            <Icon name="chevron" className="home-card-go" />
          </div>
          {noBacklog ? (
            <div className="home-clear">
              <span className="home-clear-emoji" aria-hidden>🎉</span>
              <strong>全部审核完毕</strong>
              <span className="home-card-sub">没有待审会话</span>
            </div>
          ) : (
            <>
              <div className="home-bignum">{review.pending_sessions}</div>
              <div className="home-card-sub">
                {review.pending_sessions} 处待审 · {review.pending_segments} 段
              </div>
            </>
          )}
        </button>

        {/* 人物 — roster size + enrolled voiceprints; jumps to 声纹. */}
        <button type="button" aria-label="人物" className="home-card home-card-people" onClick={onGoSpeakers}>
          <div className="home-card-head">
            <span className="home-card-label"><Icon name="person" /> 人物</span>
            <Icon name="chevron" className="home-card-go" />
          </div>
          <div className="home-bignum">{people.total}<span className="home-bignum-unit"> 人</span></div>
          <div className="home-card-sub">{people.enrolled} 已登记声纹</div>
        </button>

        {/* 覆盖 — corpus coverage stat strip + voiceprint/emotion completion. */}
        <section className="home-card home-card-coverage" aria-label="覆盖">
          <div className="home-card-head">
            <span className="home-card-label"><Icon name="check_circle" /> 覆盖</span>
          </div>
          <div className="home-stat-strip">
            <div className="home-stat"><b>{coverage.days}</b><span>天</span></div>
            <div className="home-stat"><b>{coverage.sessions}</b><span>会话</span></div>
            <div className="home-stat"><b>{coverage.segments}</b><span>段</span></div>
          </div>
          <div className="home-cov-bars">
            <div className="home-cov-row">
              <span className="home-cov-name">声纹</span>
              <span className="home-cov-track"><span className="home-cov-fill" style={{ width: `${pct(coverage.embedded, coverage.segments)}%` }} /></span>
              <span className="home-cov-pct">{pct(coverage.embedded, coverage.segments)}%</span>
            </div>
            <div className="home-cov-row">
              <span className="home-cov-name">情绪</span>
              <span className="home-cov-track"><span className="home-cov-fill emote" style={{ width: `${pct(coverage.emoted, coverage.segments)}%` }} /></span>
              <span className="home-cov-pct">{pct(coverage.emoted, coverage.segments)}%</span>
            </div>
          </div>
        </section>

        {/* 洞察 — jump to the latest day's 观点. Disabled until a day exists. */}
        <button
          type="button"
          aria-label="洞察"
          className="home-card home-card-insight"
          disabled={!latest_day}
          onClick={() => { if (latest_day) onGoLlm(latest_day); }}
        >
          <div className="home-card-head">
            <span className="home-card-label"><Icon name="viewpoint" /> 洞察</span>
            <Icon name="chevron" className="home-card-go" />
          </div>
          {latest_day ? (
            <>
              <div className="home-insight-day">{dayLabel(latest_day)}</div>
              <div className="home-card-sub">查看最新一天的观点与记忆候选</div>
            </>
          ) : (
            <div className="home-card-sub">尚无可分析的日期</div>
          )}
        </button>

        {/* 最近会话 — the 5 newest sessions; each row opens that session in 审核. */}
        <section className="home-card home-card-recent" aria-label="最近会话">
          <div className="home-card-head">
            <span className="home-card-label"><Icon name="clock" /> 最近会话</span>
          </div>
          {recent_sessions.length === 0 ? (
            <p className="home-card-sub">还没有会话，先去「录入」导入录音。</p>
          ) : (
            <ul className="home-recent">
              {recent_sessions.map((s) => (
                <li key={s.session_id}>
                  <button type="button" className="home-recent-item" onClick={() => onOpenSession(s.session_id, s.day)}>
                    <span className="home-recent-day">{dayLabel(s.day)}</span>
                    <span className="home-recent-meta">{s.segment_count} 段</span>
                    <span className={`home-recent-status status-${s.review_status}`}>{reviewStatusZh(s.review_status)}</span>
                    <Icon name="chevron" className="home-recent-go" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
