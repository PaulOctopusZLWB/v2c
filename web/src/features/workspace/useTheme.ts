import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "pcn-theme";

/** Read the theme the pre-paint script already applied to <html>, falling back to
 *  the saved value and then the product default: dark command-room mode. */
function readInitialTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* persistence is best-effort */
  }
  return "dark";
}

/** Light/dark theme controller: keeps <html data-theme> + localStorage('pcn-theme') in
 *  sync. */
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

  return { theme, set, toggle };
}
