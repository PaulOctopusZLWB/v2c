import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useAsyncAction } from "../hooks/useAsyncAction";

describe("useAsyncAction", () => {
  it("toggles pending false -> true while in flight -> false after resolve", async () => {
    let resolve!: () => void;
    const deferred = new Promise<void>((r) => { resolve = r; });
    const { result } = renderHook(() => useAsyncAction(() => deferred));

    expect(result.current.pending).toBe(false);

    let running!: Promise<void>;
    act(() => { running = result.current.run() as Promise<void>; });
    expect(result.current.pending).toBe(true);

    await act(async () => { resolve(); await running; });
    expect(result.current.pending).toBe(false);
  });

  it("clears pending even if the wrapped function rejects", async () => {
    let reject!: (e: unknown) => void;
    const deferred = new Promise<void>((_, r) => { reject = r; });
    const { result } = renderHook(() => useAsyncAction(() => deferred));

    let running!: Promise<void>;
    act(() => { running = result.current.run() as Promise<void>; });
    expect(result.current.pending).toBe(true);

    await act(async () => {
      reject(new Error("boom"));
      await running.catch(() => undefined);
    });
    expect(result.current.pending).toBe(false);
  });
});
