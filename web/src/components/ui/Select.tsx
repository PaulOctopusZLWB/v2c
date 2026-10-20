import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Portal } from "./Portal";
import { matchesPerson } from "../../lib/personSearch";

export interface SelectOption {
  value: string;
  label: string;
  searchText?: string;
}

/**
 * Accessible select whose option list renders through <Portal> (#overlay-root), so it is NEVER
 * clipped by an ancestor's overflow:hidden / scroll / transform — the bug class that broke the
 * native <select>s inside the 声纹 scroll columns. Drop-in for a native <select value onChange>:
 * keeps role=combobox + aria-label parity, keyboard (↑/↓/Enter/Esc/Home/End), and outside-click /
 * scroll dismissal. The popover is position:fixed to the trigger and stacks at var(--z-dropdown).
 */
export function Select({
  value,
  onChange,
  options,
  ariaLabel,
  placeholder = "请选择…",
  disabled,
  className,
  searchable = false,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ top: number; left: number; width: number; maxHeight: number } | null>(null);

  const selected = options.find((o) => o.value === value) ?? null;
  const filtered = options.filter((o) => matchesPerson(o, query));
  const activeIndex = Math.max(0, Math.min(active, filtered.length - 1));

  const place = useCallback(() => {
    const t = triggerRef.current;
    if (!t) return;
    const r = t.getBoundingClientRect();
    const width = Math.min(Math.max(r.width, 240), window.innerWidth - 16);
    const below = window.innerHeight - r.bottom - 8;
    const maxHeight = Math.min(320, Math.max(below, r.top - 8, 0));
    setRect({ top: below >= maxHeight ? r.bottom + 4 : Math.max(8, r.top - maxHeight - 4),
      left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)), width, maxHeight });
  }, []);

  const openMenu = useCallback(() => {
    if (disabled) return;
    place();
    setQuery("");
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  }, [disabled, place, options, value]);

  const close = useCallback(() => setOpen(false), []);
  useEffect(() => { if (disabled) close(); }, [disabled, close]);
  useEffect(() => {
    if (open) menuRef.current?.querySelector(".active")?.scrollIntoView?.({ block: "nearest" });
  }, [open, activeIndex, query]);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  // Dismiss on outside pointer-down, and on unrelated scroll/resize (a fixed popover would otherwise
  // drift from its trigger when a scroll container moves). Scrolling the portalled menu itself must
  // not close the menu.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      close();
    };
    const onScroll = (e: Event) => {
      const target = e.target;
      if (
        target instanceof Node &&
        (triggerRef.current?.contains(target) || menuRef.current?.contains(target))
      ) return;
      close();
    };
    const onResize = () => close();
    document.addEventListener("pointerdown", onDown, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open, close]);

  const choose = (v: string) => {
    if (disabled) return;
    onChange(v);
    close();
    triggerRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled || e.nativeEvent.isComposing) return;
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === "Tab") { close(); triggerRef.current?.focus(); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); triggerRef.current?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(filtered.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === "Home" && e.target === triggerRef.current) { e.preventDefault(); setActive(0); }
    else if (e.key === "End" && e.target === triggerRef.current) { e.preventDefault(); setActive(filtered.length - 1); }
    else if (e.key === "Enter" || (e.key === " " && e.target === triggerRef.current)) { e.preventDefault(); const o = filtered[activeIndex]; if (o) choose(o.value); }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && filtered.length ? `${listId}-${activeIndex}` : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        className={`ui-select${className ? ` ${className}` : ""}`}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={onKeyDown}
      >
        <span className={`ui-select-value${selected ? "" : " ui-select-placeholder"}`}>
          {selected ? selected.label : placeholder}
        </span>
        <span className="ui-select-caret" aria-hidden>▾</span>
      </button>
      {open && !disabled && rect ? (
        <Portal>
          <div
            ref={menuRef}
            className="ui-select-menu"
            style={{ position: "fixed", ...rect }}
          >
            {searchable ? <input
              autoFocus
              aria-label={`${ariaLabel ?? "选项"}搜索`}
              placeholder="姓名 / 拼音 / 首字母"
              value={query}
              aria-controls={listId}
              aria-activedescendant={filtered.length ? `${listId}-${activeIndex}` : undefined}
              onChange={(e) => { setQuery(e.target.value); setActive(0); }}
              onKeyDown={(e) => { if (!e.nativeEvent.isComposing) onKeyDown(e); }}
            /> : null}
            <ul id={listId} role="listbox" aria-label={ariaLabel} style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {filtered.map((o, i) => (
              <li
                key={o.value}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={o.value === value}
                className={`ui-select-option${i === activeIndex ? " active" : ""}${o.value === value ? " selected" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(o.value)}
              >
                {o.label}
              </li>
            ))}
            </ul>
            {!filtered.length ? <p role="status">没有匹配的人物</p> : null}
          </div>
        </Portal>
      ) : null}
    </>
  );
}
