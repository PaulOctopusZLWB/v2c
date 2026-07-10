import type { PipelineTaskMetric } from "../../api/types";
import { humanizeDuration, taskTypeZh } from "../../lib/format";
import { Icon } from "../../components/Icon";

/* 阶段耗时统计(管道控制室右栏):按 task_type 展示成功率 + P50/P95/平均耗时 + 计数,
 * 数据来自 GET /api/pipeline/metrics。任一 task_type 尚无已完成任务时 duration_ms/success_rate
 * 为 null——该行只显示计数,耗时列回退为占位符。 */

function pct(rate: number | null): string {
  return rate == null ? "—" : `${Math.round(rate * 100)}%`;
}

function dur(ms: number | null | undefined): string {
  return ms == null ? "—" : humanizeDuration(ms);
}

export function StageDurationsPanel({ metrics }: { metrics: PipelineTaskMetric[] | null | undefined }) {
  const rows = metrics ?? [];
  return (
    <section className="stage-durations" aria-label="阶段耗时">
      <div className="section-title">
        <Icon name="clock" /> 阶段耗时
      </div>
      {rows.length === 0 ? (
        <p className="stage-durations-empty dim">暂无任务数据 — 运行管道后这里会显示各阶段耗时。</p>
      ) : (
        <div className="stage-durations-rows">
          {rows.map((row) => (
            <div className="stage-duration-row" key={row.task_type}>
              <div className="stage-duration-head">
                <span className="stage-duration-name">{taskTypeZh(row.task_type)}</span>
                <span className="stage-duration-rate num">{pct(row.success_rate)}</span>
              </div>
              <div className="stage-duration-metrics num">
                <span title="P50">P50 {dur(row.duration_ms?.p50)}</span>
                <span title="P95">P95 {dur(row.duration_ms?.p95)}</span>
                <span title="平均">均 {dur(row.duration_ms?.avg)}</span>
              </div>
              <div className="stage-duration-counts">
                <span className="badge s-accepted">成功 {row.counts.succeeded}</span>
                <span className="badge s-rejected">失败 {row.counts.failed_terminal}</span>
                <span className="badge s-pending_review">待处理 {row.counts.pending}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
