import { useEffect, useState } from "react";

export type TabId = "home" | "ingest" | "review" | "speakers" | "memory" | "llm" | "settings";

const TAB_IDS: readonly TabId[] = ["home", "ingest", "review", "speakers", "memory", "llm", "settings"];
const DEFAULT_TAB: TabId = "home";

/** Reads the current tab from the URL hash (`#tab=<id>`), falling back to the default. */
function tabFromHash(): TabId {
  const raw = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("tab");
  return TAB_IDS.includes(raw as TabId) ? (raw as TabId) : DEFAULT_TAB;
}

/**
 * Workspace tab state, mirrored to the URL hash so deep links, reloads, and the
 * browser back/forward buttons all select the right tab. The hash is the single
 * source of truth — `setTab` writes it, and a `hashchange` listener syncs state
 * back when it changes externally (navigation, manual edits).
 */
export function useTab(): { tab: TabId; setTab: (t: TabId) => void } {
  const [tab, setTabState] = useState<TabId>(tabFromHash);

  useEffect(() => {
    const sync = () => setTabState(tabFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const setTab = (t: TabId) => {
    setTabState(t);
    window.location.hash = `tab=${t}`;
  };

  return { tab, setTab };
}
