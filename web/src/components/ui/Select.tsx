import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Portal } from "./Portal";

export interface SelectOption {
  value: string;
  label: string;
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
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  ariaLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);

  const selected = options.find((o) => o.value === value) ?? null;

  const place = useCallback(() => {
    const t = triggerRef.current;
    if (!t) return;
    const r = t.getBoundingClientRect();
    setRect({ top: r.bottom + 4, left: r.left, width: r.width });
  }, []);

  const openMenu = useCallback(() => {
    if (disabled) return;
    place();
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  }, [disabled, place, options, value]);

  const close = useCallback(() => setOpen(false), []);

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
    onChange(v);
    close();
    triggerRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); close(); triggerRef.current?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(options.length - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const o = options[active]; if (o) choose(o.value); }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
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
      {open && rect ? (
        <Portal>
          <ul
            ref={menuRef}
            role="listbox"
            aria-label={ariaLabel}
            className="ui-select-menu"
            style={{ position: "fixed", top: rect.top, left: rect.left, minWidth: rect.width }}
          >
            {options.map((o, i) => (
              <li
                key={o.value}
                role="option"
                aria-selected={o.value === value}
                className={`ui-select-option${i === active ? " active" : ""}${o.value === value ? " selected" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(o.value)}
              >
                {o.label}
              </li>
            ))}
          </ul>
        </Portal>
      ) : null}
    </>
  );
}
