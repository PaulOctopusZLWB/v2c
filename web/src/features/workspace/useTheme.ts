import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "pcn-theme";

/** Read the theme the pre-paint script already applied to <html>, falling back to
 *  the saved value / system preference if (e.g. in tests) the attribute is absent. */
function readInitialTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* localStorage unavailable (private mode / SSR) — fall through to system */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Light/dark theme controller: keeps <html data-theme> + localStorage('pcn-theme') in
 *  sync, and live-follows the OS only while the user hasn't made an explicit choice. */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme);

  // Reflect the current theme onto <html> so the CSS token blocks switch.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const set = useCallback((next: Theme) => {
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* persistence is best-effort */
    }
    setThemeState(next);
  }, []);

  const toggle = useCallback(() => {
    set(theme === "dark" ? "light" : "dark");
  }, [theme, set]);

  // Follow OS changes ONLY when the user never made an explicit choice.
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const onChange = (e: MediaQueryListEvent) => {
      let saved: string | null = null;
      try {
        saved = localStorage.getItem(KEY);
      } catch {
        /* ignore */
      }
      if (!saved) setThemeState(e.matches ? "dark" : "light");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return { theme, set, toggle };
}
