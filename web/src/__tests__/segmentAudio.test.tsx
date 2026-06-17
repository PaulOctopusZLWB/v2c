import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useSegmentAudio } from "../hooks/useSegmentAudio";

// jsdom has no AudioContext, so the hook uses the <audio> fallback. test-setup stubs
// HTMLMediaElement.play/pause and URL.createObjectURL.
function mockAudioFetch() {
  // Fresh Response per call — a Response body can only be consumed once.
  vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(new Response(new Blob([new Uint8Array([1, 2, 3])]), { status: 200 })),
  );
}

describe("useSegmentAudio (exclusive playback)", () => {
  it("stops the previous clip when a new one starts and tracks one shared `playing`", async () => {
    mockAudioFetch();
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause");
    const { result } = renderHook(() => useSegmentAudio());

    await act(async () => {
      await result.current.play("seg_a");
    });
    await waitFor(() => expect(result.current.playing).toBe("seg_a"));
    const pausesAfterA = pause.mock.calls.length;

    await act(async () => {
      await result.current.play("seg_b");
    });
    // Starting B paused the previously-playing A, and `playing` is now B (not both).
    expect(pause.mock.calls.length).toBeGreaterThan(pausesAfterA);
    await waitFor(() => expect(result.current.playing).toBe("seg_b"));
  });

  it("two separate hook instances share the same singleton player", async () => {
    mockAudioFetch();
    const a = renderHook(() => useSegmentAudio());
    const b = renderHook(() => useSegmentAudio());

    await act(async () => {
      await a.result.current.play("seg_x");
    });
    // The other instance (e.g. the voiceprint map vs a turn) sees the same playing clip.
    await waitFor(() => expect(b.result.current.playing).toBe("seg_x"));

    await act(async () => {
      await b.result.current.stop();
    });
    await waitFor(() => expect(a.result.current.playing).toBeNull());
  });

  it("a failed clip does not leave a stuck `playing` state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("nope", { status: 404 }));
    const { result } = renderHook(() => useSegmentAudio());
    await act(async () => {
      await expect(result.current.play("seg_404")).rejects.toThrow("404");
    });
    expect(result.current.playing).toBeNull();
  });
});
