import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ImportProgress, StatusSummary } from "../../api/types";
import { pipelineStages } from "../../lib/pipelineStages";
import { usePipelineFeed } from "../../hooks/usePipelineFeed";
import { speakerColor } from "../../lib/speakerColors";
import { timeOfDay } from "../../lib/format";

/* 管道控制室(design handoff 1b):280px 阶段栈 | 1fr 实时转写流 | 270px 任务栏。
 * 阶段栈与今日横条共用 pipelineStages 推导;实时转写流吃 segment.transcribed;
 * 右栏放运行控制/设备导入/任务列表(slots,由 App 注入)+ mono 事件流 tail。 */

export const AUTO_REVIEW_KEY = "pcn-auto-review";

export function readAutoReview(): boolean {
  try {
    return localStorage.getItem(AUTO_REVIEW_KEY) === "1";
  } catch {
    return false;
  }
}

export function PipelinePanel({
  summary,
  importProgress,
  running,
  onGoReview,
  progress,
  runInspector,
  devicePanel,
  taskList
}: {
  summary: StatusSummary | null;
  importProgress?: ImportProgress | null;
  running: boolean;
  onGoReview: () => void;
  /** 总进度(<Progress/>,含分阶段与 ETA)— 转写流头行。 */
  progress?: ReactNode;
  runInspector?: ReactNode;
  devicePanel?: ReactNode;
  taskList?: ReactNode;
}) {
  const { segments, tail, completed } = usePipelineFeed();
  const stages = pipelineStages(summary, importProgress);
  const [autoReview, setAutoReview] = useState(readAutoReview);
  const setAuto = (on: boolean) => {
    setAutoReview(on);
    try {
      localStorage.setItem(AUTO_REVIEW_KEY, on ? "1" : "0");
    } catch {
      /* private mode 等场景忽略 */
    }
  };

  // 新段进入时把流滚到底(列表底对齐,最新在最下)。
  const feedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    feedRef.current?.scrollTo?.({ top: feedRef.current.scrollHeight });
  }, [segments.length]);

  return (
    <div className="tab-page pipeline-layout">
      {/* 左:阶段栈。 */}
      <aside className="pipe-stages" aria-label="管道阶段">
        {stages.map((stage) => (
          <div key={stage.label} className={`pipe-stage is-${stage.state}`}>
            <div className="pipe-stage-head">
              {stage.state === "done" ? <span className="pipe-stage-mark ok" aria-hidden>✓</span> : null}
              {stage.state === "running" ? <span className="breathe-dot" aria-hidden /> : null}
              {stage.state === "pending" ? <span className="pipe-stage-mark dim" aria-hidden>○</span> : null}
              <span className="pipe-stage-name">{stage.label}</span>
              {stage.total > 0 ? (
                <span className="pipe-stage-count num">{stage.done}/{stage.total}</span>
              ) : null}
            </div>
            {stage.state === "running" ? (
              <span className="pipe-stage-track">
                <span className="pipe-stage-fill" style={{ width: `${stage.pct ?? 0}%` }} />
              </span>
            ) : null}
          </div>
        ))}
        <label className="pipe-auto rf-toggle">
          <input type="checkbox" checked={autoReview} onChange={(e) => setAuto(e.target.checked)} />
          <span>完成后自动跳转审核</span>
        </label>
      </aside>

      {/* 中:实时转写流。 */}
      <section className="pipe-feed-col" aria-label="实时转写">
        <div className="pipe-feed-head">
          <span className={running ? "pipe-live num" : "pipe-live num dim"}>
            实时转写 {running ? "LIVE" : ""}
          </span>
          {progress}
        </div>
        <div className="pipe-feed panel-scroll" ref={feedRef}>
          {segments.length === 0 ? (
            <p className="pipe-feed-empty dim">
              {running ? "等待新转写段…" : "管道空闲 — 在右侧导入音频并运行后,这里会实时滚动转写结果。"}
            </p>
          ) : (
            segments.map((seg, i) => {
              const isNewest = i === segments.length - 1;
              return (
                <div
                  className={`pipe-row segment-enter${isNewest && running ? " is-live" : ""}`}
                  key={seg.segment_id}
                  style={{ opacity: 0.35 + 0.65 * ((i + 1) / segments.length) }}
                >
                  <span className="pipe-row-time num">{timeOfDay(seg.absolute_start_at) || `${Math.round(seg.start_ms / 1000)}s`}</span>
                  <span className="chip" style={{ background: speakerColor(seg.speaker) }}>{seg.speaker}</span>
                  <span className="pipe-row-text">
                    {seg.text}
                    {isNewest && running ? <span className="pipe-caret" aria-hidden /> : null}
                  </span>
                </div>
              );
            })
          )}
        </div>
        {completed ? (
          <div className="pipe-complete">
            <span className="pipe-complete-copy">
              ✓ 转写完成 · <span className="num">{completed.done_total}/{completed.total}</span>
              {completed.failed_total ? <span className="warn"> · 失败 {completed.failed_total}</span> : null}
            </span>
            <button type="button" className="primary" onClick={onGoReview}>
              立即审核 <kbd className="key-hint">↵</kbd>
            </button>
          </div>
        ) : null}
      </section>

      {/* 右:任务栏(运行控制 / 设备导入 / 任务列表 / 事件流 tail)。 */}
      <aside className="pipe-tasks" aria-label="任务栏">
        {runInspector}
        {devicePanel}
        {taskList}
        {tail.length ? (
          <div className="pipe-tail num" aria-label="事件流">
            {tail.slice(-12).map((entry) => (
              <div key={entry.id} className={`pipe-tail-row is-${entry.kind}`}>{entry.label}</div>
            ))}
          </div>
        ) : null}
      </aside>
    </div>
  );
}
