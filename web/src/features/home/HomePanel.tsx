import { Icon } from "../../components/Icon";
import { reviewStatusZh, timeOfDay } from "../../lib/format";
import { pipelineStages } from "../../lib/pipelineStages";
import type { HomeOverview, ImportProgress, StatusSummary } from "../../api/types";

/* 今日(首页,design handoff 1a):标题行 + 三卡行(待审 hero / 人物 / 覆盖)
 * + 管道横向 spine + 最近会话表。数据由 App 注入(overview 提升到 App,
 * 与侧栏徽标共用一次获取);「待确认记忆卡」等 Phase 5 有真实数据后原位替换人物卡。 */

interface HomePanelProps {
  overview: HomeOverview | null;
  error: string | null;
  /** 管道 spine 的数据源(SSE status.summary);null = 尚无任务信息。 */
  summary: StatusSummary | null;
  importProgress?: ImportProgress | null;
  running: boolean;
  onStartReview: () => void;
  onGoPeople: () => void;
  onGoPipeline: () => void;
  onOpenSession: (sessionId: string, day: string) => void;
}

/** Percentage of `part`/`whole` as a rounded integer (0 when there's nothing to cover). */
function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

/** 「7 月 7 日 周二」 — page title for today. */
function todayZh(): string {
  const now = new Date();
  const wd = ["日", "一", "二", "三", "四", "五", "六"];
  return `${now.getMonth() + 1} 月 ${now.getDate()} 日 周${wd[now.getDay()]}`;
}

/** 语音时长的中文表述:「2 小时 41 分」/「18 分钟」/「45 秒」。 */
function speechDurationZh(ms: number): string {
  const mins = Math.round(ms / 60_000);
  if (mins < 1) return `${Math.max(1, Math.round(ms / 1000))} 秒`;
  if (mins < 60) return `${mins} 分钟`;
  return `${Math.floor(mins / 60)} 小时 ${mins % 60} 分`;
}

// 六阶段推导移到 lib/pipelineStages(管道页阶段栈复用);这里保留 re-export 供测试与旧引用。
export { pipelineStages as spineStages } from "../../lib/pipelineStages";

export function HomePanel({
  overview,
  error,
  summary,
  importProgress,
  running,
  onStartReview,
  onGoPeople,
  onGoPipeline,
  onOpenSession
}: HomePanelProps) {
  if (error) {
    return (
      <div className="tab-page single home">
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
      <div className="tab-page single home">
        <div className="empty">
          <Icon name="refresh" className="empty-icon" />
          <h3>正在加载概览…</h3>
        </div>
      </div>
    );
  }

  const { review, people, coverage, recent_sessions } = overview;
  const noBacklog = review.pending_sessions === 0;
  const stages = pipelineStages(summary, importProgress);
  const spineLive = running || !!importProgress?.active;

  return (
    <div className="tab-page single home">
      <div className="today-head">
        <h2 className="today-title">{todayZh()}</h2>
        <span className="today-sub dim">
          {overview.today && overview.today.segments > 0 ? (
            <>已录 <span className="num">{overview.today.segments}</span> 段 · {speechDurationZh(overview.today.speech_ms)}</>
          ) : (
            "今日暂无录音"
          )}
        </span>
      </div>

      <div className="today-cards">
        {/* 待审 hero:accent 描边 + 左上角辉光;↵ 全局快捷键也指向开始审核。 */}
        <section className="today-card today-card-review" aria-label="待审">
          <span className="today-glow" aria-hidden />
          <div className="today-card-label is-accent">待审</div>
          {noBacklog ? (
            <>
              <div className="today-num-row">
                <span className="today-bignum num">0</span>
                <span className="today-num-sub">会话</span>
              </div>
              <div className="today-hint"><Icon name="check_circle" /> 全部审核完毕</div>
            </>
          ) : (
            <>
              <div className="today-num-row">
                <span className="today-bignum num">{review.pending_sessions}</span>
                <span className="today-num-sub">会话 · {review.pending_segments} 段</span>
              </div>
              <div className="today-hint">还有 <b className="num">{review.pending_segments}</b> 段待人工确认</div>
            </>
          )}
          <div className="today-card-actions">
            <button type="button" className="primary" onClick={onStartReview}>
              开始审核 <kbd className="key-hint">↵</kbd>
            </button>
            <button type="button" onClick={onStartReview}>查看队列</button>
          </div>
        </section>

        {/* 人物(Phase 5 起原位换成「待确认记忆」卡)— 整卡可点 → 声纹。 */}
        <button type="button" className="today-card today-card-people" aria-label="人物" onClick={onGoPeople}>
          <div className="today-card-label">人物</div>
          <div className="today-num-row">
            <span className="today-bignum num">{people.total}</span>
            <span className="today-num-sub">人 · 已登记 {people.enrolled}</span>
          </div>
          <div className="today-hint">在声纹页登记与指认 →</div>
        </button>

        {/* 覆盖:天/会话/段 + 声纹/情绪覆盖率两条 3px 进度线。 */}
        <section className="today-card today-card-coverage" aria-label="覆盖">
          <div className="today-card-label">覆盖</div>
          <div className="today-stats">
            <div className="today-stat"><b className="num">{coverage.days}</b><span>天</span></div>
            <div className="today-stat"><b className="num">{coverage.sessions}</b><span>会话</span></div>
            <div className="today-stat"><b className="num">{coverage.segments}</b><span>段</span></div>
          </div>
          <div className="today-cov">
            <div className="today-cov-row">
              <span className="today-cov-name">声纹</span>
              <span className="today-cov-track"><span className="today-cov-fill" style={{ width: `${pct(coverage.embedded, coverage.segments)}%` }} /></span>
              <span className="num">{pct(coverage.embedded, coverage.segments)}%</span>
            </div>
            <div className="today-cov-row">
              <span className="today-cov-name">情绪</span>
              <span className="today-cov-track"><span className="today-cov-fill is-emote" style={{ width: `${pct(coverage.emoted, coverage.segments)}%` }} /></span>
              <span className="num">{pct(coverage.emoted, coverage.segments)}%</span>
            </div>
          </div>
        </section>
      </div>

      {/* 管道横条(玻璃底):六阶段 spine,整条可点 → 管道页。 */}
      <button type="button" className="today-spine" aria-label="管道" onClick={onGoPipeline}>
        <div className="today-spine-head">
          <span className="today-card-label">管道</span>
          <span className={spineLive ? "today-spine-target" : "today-spine-target dim"}>
            {spineLive
              ? `${importProgress?.active ? importProgress.current || "导入中" : summary?.current_target ?? ""} 处理中`
              : "管道空闲"}
          </span>
          <span className="today-spine-more dim">在管道页查看 →</span>
        </div>
        <div className="today-spine-track">
          {stages.map((st, i) => (
            <span className="today-spine-cell" key={st.label}>
              {i > 0 ? <span className={`today-spine-link is-${st.state}`} aria-hidden /> : null}
              <span className={`today-spine-stage is-${st.state}`}>
                {st.state === "done" ? <span aria-hidden>✓ </span> : null}
                {st.state === "running" ? <span className="breathe-dot breathe-dot--sm" aria-hidden /> : null}
                {st.label}
                {st.state === "running" && st.pct !== undefined ? <span className="num"> {st.pct}%</span> : null}
              </span>
            </span>
          ))}
        </div>
      </button>

      {/* 最近会话表:时间 / 名称 / 参与人 / 状态 / ↵ 打开。 */}
      <section className="today-recent" aria-label="最近会话">
        <div className="today-card-label">最近会话</div>
        {recent_sessions.length === 0 ? (
          <p className="dim">还没有会话,先去「管道」导入录音。</p>
        ) : (
          <div className="today-table">
            {recent_sessions.map((s) => {
              // blocked 同时覆盖「还在处理」与「有 needs_fix 段」两种情况 —— 用
              // reviewStatusZh 的「受阻」而不是误导性的「处理中」。
              const statusText =
                s.review_status === "accepted"
                  ? "已审"
                  : s.review_status === "pending_review"
                    ? `待审 ${s.pending_segments ?? ""}`.trim()
                    : reviewStatusZh(s.review_status);
              return (
                <button type="button" className="today-row" key={s.session_id} onClick={() => onOpenSession(s.session_id, s.day)}>
                  <span className="today-row-time num">{timeOfDay(s.started_at) || s.day}</span>
                  <span className="today-row-name">{s.name?.trim() || `会话 · ${s.segment_count} 段`}</span>
                  <span className="today-row-people dim">{s.participants ?? "—"}</span>
                  <span className={`today-row-status num is-${s.review_status}`}>{statusText}</span>
                  <span className="today-row-open dim">↵ 打开</span>
                </button>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
