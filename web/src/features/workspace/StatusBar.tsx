import type { ReactNode } from "react";
import type { ImportProgress, StatusSummary } from "../../api/types";
import { taskTypeZh } from "../../lib/format";
import { ThemeToggle } from "./ThemeToggle";
import type { Theme } from "./useTheme";

/* 46px 玻璃全局状态条:左侧当前页名,右侧管道状态胶囊 + 主题切换。
 * 胶囊文案(设计稿):运行中 「{阶段} · {目标} {pct}% · 剩约 X 分」,空闲 「管道空闲」;
 * 点击胶囊跳管道页。明细进度(分阶段/ETA 条)住在管道页,不在这里。 */

/** 剩余时间(数字走 mono/.num,避免逐秒跳动时胶囊宽度抖动)。 */
function etaZh(seconds: number): ReactNode {
  if (seconds < 90) return <>剩约 <span className="num">1</span> 分</>;
  const mins = Math.round(seconds / 60);
  if (mins < 60) return <>剩约 <span className="num">{mins}</span> 分</>;
  return <>剩约 <span className="num">{Math.round(mins / 60)}</span> 小时</>;
}

export function StatusBar({
  pageTitle,
  summary,
  importProgress,
  running,
  onGoPipeline,
  theme,
  onToggleTheme
}: {
  pageTitle: string;
  summary: StatusSummary | null;
  importProgress?: ImportProgress | null;
  running: boolean;
  onGoPipeline: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  const importing = !!importProgress?.active;
  const featureProgress = summary?.feature_progress?.active ? summary.feature_progress : null;
  const total = importing ? importProgress!.total : featureProgress?.total ?? summary?.total ?? 0;
  const done = importing ? importProgress!.done : featureProgress?.done ?? summary?.done_total ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  // 数字/ID 一律走 .num(mono + tabular-nums),百分比逐秒变化时胶囊不抖。
  let pillContent: ReactNode;
  if (importing) {
    pillContent = importProgress?.phase === "scanning"
      ? <>正在扫描源文件</>
      : <>导入新增 · <span className="num">{importProgress!.done}/{importProgress!.total} {pct}%</span></>;
  } else if (running && featureProgress) {
    pillContent = (
      <>
        声纹/情绪提取 · <span className="num">{featureProgress.current || featureProgress.target_id} {pct}%</span>
      </>
    );
  } else if (running && summary?.active_stage) {
    pillContent = (
      <>
        {taskTypeZh(summary.active_stage)}中 ·
        {summary.current_target ? <span className="num"> {summary.current_target}</span> : null}
        <span className="num"> {pct}%</span>
        {summary.eta_seconds != null ? <> · {etaZh(summary.eta_seconds)}</> : null}
      </>
    );
  } else if (running) {
    pillContent = <>运行中 · <span className="num">{pct}%</span></>;
  } else {
    pillContent = "管道空闲";
  }
  const live = importing || running;

  return (
    <header className="statusbar">
      <span className="statusbar-title">{pageTitle}</span>
      <div className="statusbar-end">
        <button
          type="button"
          className={live ? "statusbar-pill live" : "statusbar-pill"}
          onClick={onGoPipeline}
          title="打开管道页"
        >
          {live ? <span className="breathe-dot" aria-hidden /> : null}
          <span className={live ? "" : "dim"}>{pillContent}</span>
        </button>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
    </header>
  );
}
