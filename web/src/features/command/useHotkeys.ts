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

/**
 * True when the event originated from a control the keystroke should defer to: an
 * editable field the user is typing into (INPUT/TEXTAREA/SELECT/contentEditable) OR
 * a focused activatable control (BUTTON, a link with href, or an element with an
 * activatable ARIA role). Deferring to the latter is an accessibility fix — Space on
 * a focused <button> must activate the button, not fire a global hotkey.
 */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  // A focused activatable control owns single-key activation (Space/Enter).
  if (tag === "BUTTON") return true;
  if (tag === "A" && target.hasAttribute("href")) return true;
  const role = target.getAttribute("role");
  if (role && ACTIVATABLE_ROLES.has(role)) return true;
  return false;
}

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
      const combo = eventToCombo(e);
      const handler = bindingsRef.current[combo];
      if (!handler) return;
      if (!ALWAYS_ACTIVE.has(combo) && isEditableTarget(e.target)) return;
      handler(e);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // Rebind when the caller passes a new bindings object or toggles enabled.
  }, [bindings, enabled]);
}
