import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Portal } from "../../components/ui/Portal";

export interface Command {
  id: string;
  title: string;
  hint?: string;
  group?: string;
  keywords?: string;
  /** Optional rich title (e.g. a snippet with the matched substring bolded). Falls back to `title`. */
  node?: ReactNode;
  run: () => void;
}

const DEFAULT_GROUP = "命令";

/**
 * Case-insensitive subsequence (fuzzy) match: every char of `query` must appear in
 * `haystack` in order. An empty query matches everything. Falls back gracefully to a
 * plain substring for contiguous queries — subsequence is a superset of that.
 */
function fuzzyMatch(query: string, haystack: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const h = haystack.toLowerCase();
  let qi = 0;
  for (let hi = 0; hi < h.length && qi < q.length; hi++) {
    if (h[hi] === q[qi]) qi++;
  }
  return qi === q.length;
}

/**
 * ⌘K launcher overlay. Renders only when `open`; locally fuzzy-filters `commands`.
 *
 * Search mode: the parent observes the typed query via `onQueryChange` (to debounce an async
 * search) and injects the results as `extraItems`. Those are already server-filtered, so they
 * are appended UNFILTERED after the locally-matched commands and share the same flat index space
 * for arrow navigation.
 */
export function CommandPalette({
  open,
  commands,
  onClose,
  extraItems = [],
  onQueryChange
}: {
  open: boolean;
  commands: Command[];
  onClose: () => void;
  extraItems?: Command[];
  onQueryChange?: (q: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset query/highlight every time the palette opens, and focus the input.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      onQueryChange?.("");
      // Focus after the panel mounts so autofocus lands reliably.
      const id = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim();
    const local = commands.filter((c) => fuzzyMatch(q, `${c.title} ${c.keywords ?? ""} ${c.group ?? ""}`));
    // extraItems are server-filtered async search hits — never run the local fuzzy gate on them.
    // 设计稿:「语义检索」组排在「命令」之前,且 Enter 默认执行首个检索命中(↵ 跳转)。
    return [...extraItems, ...local];
  }, [commands, extraItems, query]);

  function changeQuery(next: string) {
    setQuery(next);
    onQueryChange?.(next);
  }

  // Keep the highlight in range as the filtered list shrinks/grows.
  useEffect(() => {
    setActive((i) => (filtered.length === 0 ? 0 : Math.min(i, filtered.length - 1)));
  }, [filtered.length]);

  // Group while preserving the flat order, so arrow navigation and the rendered groups
  // share one index space.
  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup = new Map<string, Command[]>();
    for (const c of filtered) {
      const g = c.group ?? DEFAULT_GROUP;
      if (!byGroup.has(g)) {
        byGroup.set(g, []);
        order.push(g);
      }
      byGroup.get(g)!.push(c);
    }
    return order.map((g) => ({ group: g, items: byGroup.get(g)! }));
  }, [filtered]);

  if (!open) return null;

  function runAt(index: number) {
    const cmd = filtered[index];
    if (!cmd) return;
    cmd.run();
    onClose();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (filtered.length === 0 ? 0 : (i + 1) % filtered.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (filtered.length === 0 ? 0 : (i - 1 + filtered.length) % filtered.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      runAt(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  // The flat index of the first item in each group, so per-group rendering can map back
  // to the shared `active`/`filtered` index space.
  let flatIndex = 0;

  return (
    <Portal>
    <div className="cmdk-overlay" onMouseDown={onClose}>
      <div
        className="cmdk-panel"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="cmdk-input"
          type="text"
          role="textbox"
          aria-label="搜索命令"
          placeholder="搜索标签页、操作,或直接提问…"
          value={query}
          onChange={(e) => changeQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="cmdk-list" role="listbox">
          {filtered.length === 0 ? (
            <div className="cmdk-empty">无匹配命令</div>
          ) : (
            groups.map(({ group, items }) => (
              <div className="cmdk-group" key={group}>
                <div className="cmdk-group-head">{group}</div>
                {items.map((cmd) => {
                  const index = flatIndex++;
                  const isActive = index === active;
                  return (
                    <button
                      key={cmd.id}
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      className={isActive ? "cmdk-item active" : "cmdk-item"}
                      onMouseMove={() => setActive(index)}
                      onClick={() => runAt(index)}
                    >
                      <span className="cmdk-title">{cmd.node ?? cmd.title}</span>
                      {cmd.hint ? <span className="cmdk-hint">{cmd.hint}</span> : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
    </Portal>
  );
}
