import type { TabId } from "./useTab";

const TABS: { id: TabId; label: string }[] = [
  { id: "home", label: "首页" },
  { id: "ingest", label: "录入" },
  { id: "speakers", label: "身份" },
  { id: "review", label: "转写审核" },
  { id: "llm", label: "总结" },
  { id: "settings", label: "设置" }
];

export function Tabs({ active, onSelect }: { active: TabId; onSelect: (t: TabId) => void }) {
  return (
    <nav className="tabs" role="tablist">
      {TABS.map(({ id, label }) => {
        const isActive = id === active;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            className={isActive ? "tab active" : "tab"}
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSelect(id)}
          >
            {label}
          </button>
        );
      })}
    </nav>
  );
}
