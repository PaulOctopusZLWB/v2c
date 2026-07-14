import type { TabId } from "./useTab";
import { dayLabel } from "../../lib/format";

/* 216px 主导航侧栏(design handoff 全局框架)。
 * 5 个主项带数字快捷键 1–5;审核项挂待办数徽标、管道项在运行时亮呼吸点;
 * 「资料库」列出最近日期(点击 → 审核页该天);设置钉底,其下是节点状态行。
 * 语义:保持 role="tablist"/"tab" —— 这是同页视图切换,不是页面间导航。 */

export interface SidebarNavItem {
  id: TabId;
  label: string;
  /** 设计稿占位字形(◆▶☰◉❖⚙);后续可换 Icon.tsx 体系。 */
  glyph: string;
  key?: string;
}

export const SIDEBAR_NAV: SidebarNavItem[] = [
  { id: "home", label: "今日", glyph: "◆", key: "1" },
  { id: "ingest", label: "管道", glyph: "▶", key: "2" },
  { id: "review", label: "审核", glyph: "☰", key: "3" },
  { id: "speakers", label: "声纹", glyph: "◉", key: "4" },
  { id: "memory", label: "记忆", glyph: "❖", key: "5" },
  { id: "llm", label: "总结", glyph: "≡", key: "6" }
];

export function Sidebar({
  active,
  onSelect,
  onOpenPalette,
  pipelineRunning,
  reviewPending,
  memoryPending,
  days,
  onOpenDay
}: {
  active: TabId;
  onSelect: (t: TabId) => void;
  onOpenPalette: () => void;
  /** 管道有在途任务时,管道项右侧亮呼吸点。 */
  pipelineRunning: boolean;
  /** 待审会话数;>0 时在审核项右侧显示 warn 胶囊。 */
  reviewPending?: number;
  /** 待确认记忆数;>0 时在记忆项右侧显示 warn 胶囊。 */
  memoryPending?: number;
  /** 资料库:最近的日期(最新在前),点击跳到审核页该天。 */
  days: Array<{ day: string; session_count: number }>;
  onOpenDay: (day: string) => void;
}) {
  const recentDays = days.slice(0, 4);
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <span className="sidebar-logo-mark" aria-hidden>知</span>
        <span className="sidebar-logo-name">知迹</span>
      </div>

      <button type="button" className="sidebar-search" onClick={onOpenPalette}>
        <span className="sidebar-search-hint">搜索或跳转…</span>
        <kbd className="sidebar-search-key">⌘K</kbd>
      </button>

      {/* aria-owns 把钉底的设置项并入这个 tablist(DOM 上它在 .sidebar-bottom)。 */}
      <nav className="sidebar-nav" role="tablist" aria-label="主导航" aria-owns="sidebar-tab-settings">
        {SIDEBAR_NAV.map((item) => {
          const isActive = item.id === active;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-label={item.label}
              aria-current={isActive ? "page" : undefined}
              aria-selected={isActive}
              className={isActive ? "sidebar-item active" : "sidebar-item"}
              onClick={() => onSelect(item.id)}
            >
              <span className="sidebar-item-glyph" aria-hidden>{item.glyph}</span>
              <span className="sidebar-item-label">{item.label}</span>
              <span className="sidebar-item-end">
                {item.id === "ingest" && pipelineRunning ? <span className="breathe-dot" aria-label="运行中" /> : null}
                {item.id === "review" && reviewPending ? (
                  <span className="sidebar-badge num">{reviewPending}</span>
                ) : null}
                {item.id === "memory" && memoryPending ? (
                  <span className="sidebar-badge num">{memoryPending}</span>
                ) : null}
                {item.key ? <kbd className="sidebar-item-key">{item.key}</kbd> : null}
              </span>
            </button>
          );
        })}
      </nav>

      {recentDays.length ? (
        <>
          <div className="sidebar-section-head">资料库</div>
          <div className="sidebar-days">
            {recentDays.map((d) => (
              <button key={d.day} type="button" className="sidebar-day" onClick={() => onOpenDay(d.day)}>
                <span>{dayLabel(d.day)}</span>
                <span className="sidebar-day-count num">{d.session_count}</span>
              </button>
            ))}
          </div>
        </>
      ) : null}

      <div className="sidebar-bottom">
        <button
          type="button"
          id="sidebar-tab-settings"
          role="tab"
          aria-label="设置"
          aria-current={active === "settings" ? "page" : undefined}
          aria-selected={active === "settings"}
          className={active === "settings" ? "sidebar-item active" : "sidebar-item"}
          onClick={() => onSelect("settings")}
        >
          <span className="sidebar-item-glyph" aria-hidden>⚙</span>
          <span className="sidebar-item-label">设置</span>
        </button>
        <div className="sidebar-node">
          <span className="sidebar-node-dot" aria-hidden />
          本地节点 · {window.location.host || "127.0.0.1:8765"}
        </div>
      </div>
    </aside>
  );
}
