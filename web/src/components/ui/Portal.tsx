import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Renders children into #overlay-root — a sibling of #root, OUTSIDE the app shell's overflow:hidden.
 * This is the single mechanism by which floating UI (dropdowns, menus, tooltips, command palette,
 * toasts, dialogs) escapes clipping/transform/stacking ancestors. Stacking is then governed solely
 * by the z-index token scale (see theme.css). If #overlay-root is absent (e.g. a unit test that
 * renders a component in isolation), it is created lazily so the overlay still mounts.
 */
export function Portal({ children }: { children: ReactNode }) {
  const [host] = useState<HTMLElement>(() => {
    const existing = document.getElementById("overlay-root");
    if (existing) return existing;
    const created = document.createElement("div");
    created.id = "overlay-root";
    document.body.appendChild(created);
    return created;
  });
  return createPortal(children, host);
}
