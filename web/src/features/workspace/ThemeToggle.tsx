import { Icon } from "../../components/Icon";
import type { Theme } from "./useTheme";

/** Compact sun/moon button in the app header: flips <html data-theme> between
 *  'light' and 'dark' and persists the choice to localStorage('pcn-theme').
 *  Shows the icon for the theme you'd switch TO (sun while dark, moon while light). */
export function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      className="icon-btn theme-toggle"
      aria-pressed={isDark}
      aria-label={isDark ? "切换到亮色主题" : "切换到暗色主题"}
      title="切换主题"
      onClick={onToggle}
    >
      <Icon name={isDark ? "sun" : "moon"} />
    </button>
  );
}
