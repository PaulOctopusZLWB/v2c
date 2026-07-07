import { useEffect, useRef } from "react";

/**
 * Build a normalized combo string from a keyboard event, e.g. "mod+k", "escape",
 * "shift+/", "j". `mod` collapses metaKey (mac ⌘) and ctrlKey into one token so a
 * single binding works on both platforms. The key itself is lowercased so matching
 * is case-insensitive on letters.
 */
export function eventToCombo(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.metaKey || e.ctrlKey) parts.push("mod");
  if (e.altKey) parts.push("alt");
  if (e.shiftKey) parts.push("shift");
  // Named keys (Escape, Enter, …) come through `key` as a word; letters/symbols as the char.
  // Space arrives as " " — name it so callers can bind the readable token "space".
  const key = e.key === " " ? "space" : e.key.toLowerCase();
  parts.push(key);
  return parts.join("+");
}

/** Combos that fire even while focus is in a text field (so the palette is always reachable). */
const ALWAYS_ACTIVE = new Set(["mod+k", "escape"]);

/** Activatable-control roles that own a single-key activation (Space/Enter). */
const ACTIVATABLE_ROLES = new Set(["button", "checkbox", "tab", "menuitem"]);

/** True when the user is typing here: every non-ALWAYS_ACTIVE combo must defer. */
function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable;
}

/**
 * True for a focused activatable control (BUTTON, a link with href, or an element
 * with an activatable ARIA role). These own ONLY their native activation keys —
 * Space/Enter must activate the button, but letter/digit hotkeys (1-5, t, j/k…)
 * still fire; otherwise every click parks focus on a button and kills the keyboard
 * flow (整个新 UI 都是按钮:侧栏项、卡片、表行).
 */
function isActivatableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "BUTTON") return true;
  if (tag === "A" && target.hasAttribute("href")) return true;
  const role = target.getAttribute("role");
  return !!role && ACTIVATABLE_ROLES.has(role);
}

/** Keys an activatable control natively responds to (must not be hijacked). */
const ACTIVATION_KEYS = new Set(["enter", "space"]);

/**
 * Attach a single window keydown listener that dispatches to `bindings` keyed by
 * normalized combo (see {@link eventToCombo}). Keystrokes whose target is an
 * editable field are ignored UNLESS the combo is in {@link ALWAYS_ACTIVE}
 * (mod+k / escape), which must work everywhere. The listener is torn down on
 * unmount and rebound whenever `bindings` or `enabled` change.
 */
export function useHotkeys(
  bindings: Record<string, (e: KeyboardEvent) => void>,
  opts?: { enabled?: boolean }
): void {
  const enabled = opts?.enabled ?? true;
  // Keep the latest bindings in a ref so the listener always sees current handlers
  // without re-subscribing on every render; the effect below still rebinds when the
  // identity of `bindings` changes (per the test contract).
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  useEffect(() => {
    if (!enabled) return;
    function onKeyDown(e: KeyboardEvent) {
      // An upper layer (⌘K 面板、Dialog、帮助面板)已消费的键不再触发全局热键 —
      // 消费方约定调用 preventDefault(),这里统一跳过,避免一次 Esc 关两层。
      if (e.defaultPrevented) return;
      // 输入法组合中的按键(以及 Safari 提交组合时的 keyCode 229)属于 IME,不拦截。
      if (e.isComposing || e.keyCode === 229) return;
      const combo = eventToCombo(e);
      const handler = bindingsRef.current[combo];
      if (!handler) return;
      if (!ALWAYS_ACTIVE.has(combo)) {
        // Two-tier deferral: typing fields swallow everything; activatable controls
        // (buttons/links/tabs) swallow only their native activation keys.
        if (isTextEntryTarget(e.target)) return;
        if (isActivatableTarget(e.target) && ACTIVATION_KEYS.has(combo)) return;
      }
      handler(e);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // Rebind when the caller passes a new bindings object or toggles enabled.
  }, [bindings, enabled]);
}
