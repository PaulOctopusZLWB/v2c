import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "../features/workspace/ThemeToggle";
import { useTheme } from "../features/workspace/useTheme";

// jsdom in this project ships without localStorage or matchMedia; the app guards both,
// but to assert persistence we install a minimal in-memory localStorage + a matchMedia
// stub for the duration of these tests, then restore.
function installStubs() {
  const store = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear()
  };
  vi.stubGlobal("localStorage", ls);
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn()
    }))
  );
  return ls;
}

function ThemeHarness() {
  const theme = useTheme();
  return <ThemeToggle theme={theme.theme} onToggle={theme.toggle} />;
}

describe("ThemeToggle", () => {
  beforeEach(() => {
    installStubs();
    document.documentElement.dataset.theme = "dark";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete document.documentElement.dataset.theme;
  });

  it("defaults to dark when no saved theme exists", () => {
    localStorage.removeItem("pcn-theme");
    document.documentElement.removeAttribute("data-theme");
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn()
      }))
    );

    render(<ThemeHarness />);

    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("renders a pressed sun toggle while in dark theme", () => {
    render(<ThemeHarness />);
    const btn = screen.getByRole("button", { name: "切换到亮色主题" });
    expect(btn).toHaveAttribute("aria-pressed", "true");
  });

  it("flips <html data-theme> dark→light and persists to localStorage('pcn-theme')", async () => {
    render(<ThemeHarness />);
    expect(document.documentElement.dataset.theme).toBe("dark");

    await userEvent.click(screen.getByRole("button", { name: "切换到亮色主题" }));

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("pcn-theme")).toBe("light");
    // The button now offers the reverse action.
    expect(screen.getByRole("button", { name: "切换到暗色主题" })).toHaveAttribute("aria-pressed", "false");
  });

  it("flips back light→dark on a second click", async () => {
    document.documentElement.dataset.theme = "light";
    render(<ThemeHarness />);

    await userEvent.click(screen.getByRole("button", { name: "切换到暗色主题" }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("pcn-theme")).toBe("dark");
  });
});

describe("theme.css token foundation", () => {
  // Read the real stylesheet from disk (vitest runs with cwd = the web/ package root);
  // ?raw imports resolve to an empty module under this jsdom/vitest transform.
  const css = readFileSync(resolve(process.cwd(), "src/theme.css"), "utf8");

  it("has the combined input/textarea/select white-box base rule reading --panel-2", () => {
    // The structural cure: textarea is in the SAME base selector as input+select, so no
    // control can fall through to the UA-default white box in either theme.
    expect(css).toMatch(/input\s*,\s*textarea\s*,\s*select\s*\{[^}]*var\(--panel-2\)/);
    expect(css).toMatch(/input\s*,\s*textarea\s*,\s*select\s*\{[^}]*color:\s*var\(--text\)/);
    expect(css).toMatch(/input\s*,\s*textarea\s*,\s*select\s*\{[^}]*border:\s*1px solid var\(--border-strong\)/);
  });

  it("defines both a LIGHT default block and a [data-theme=\"dark\"] block with color-scheme", () => {
    expect(css).toMatch(/:root[\s\S]*color-scheme:\s*light/);
    expect(css).toMatch(/\[data-theme="dark"\][\s\S]*color-scheme:\s*dark/);
  });

  it("keeps every legacy alias (--panel/--panel-2/--ok/--err/--shadow) defined in both themes", () => {
    // Two definitions each = one per theme block (light + dark), so old refs resolve everywhere.
    for (const token of ["--panel:", "--panel-2:", "--ok:", "--err:", "--shadow:"]) {
      const count = css.split(token).length - 1;
      expect(count, `${token} should be defined in both themes`).toBeGreaterThanOrEqual(2);
    }
  });
});

describe("index.html pre-paint theme", () => {
  const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");

  it("defaults unsaved users to dark before the app bundle runs", () => {
    expect(html).toContain('saved === "light" || saved === "dark" ? saved : "dark"');
    expect(html).not.toContain("prefers-color-scheme");
  });
});
