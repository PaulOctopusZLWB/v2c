import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useTab } from "../features/workspace/useTab";

describe("useTab", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("defaults to the inbox (每次开完会打开看到的就是待定稿会话)", () => {
    const { result } = renderHook(() => useTab());
    expect(result.current.tab).toBe("inbox");
  });

  it("initializes from an existing #tab=speakers hash", () => {
    window.location.hash = "#tab=speakers";
    const { result } = renderHook(() => useTab());
    expect(result.current.tab).toBe("speakers");
  });

  it("setTab updates state and writes the hash", () => {
    const { result } = renderHook(() => useTab());
    act(() => result.current.setTab("llm"));
    expect(result.current.tab).toBe("llm");
    expect(window.location.hash).toContain("tab=llm");
  });

  it("syncs when the hash changes externally", () => {
    const { result } = renderHook(() => useTab());
    act(() => {
      window.location.hash = "#tab=settings";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(result.current.tab).toBe("settings");
  });

  it("ignores an invalid tab id and keeps the default", () => {
    window.location.hash = "#tab=bogus";
    const { result } = renderHook(() => useTab());
    expect(result.current.tab).toBe("inbox");
  });
});
