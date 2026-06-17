import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { clipGain, setLoudnessLeveling, useSegmentAudio } from "../hooks/useSegmentAudio";

function fakeBuf(peak: number) {
  const data = new Float32Array([0, peak, -peak / 2, 0]);
  return { numberOfChannels: 1, getChannelData: () => data } as unknown as AudioBuffer;
}

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

  it("loudness leveling peak-normalizes quiet clips and leaves silence/loud alone", () => {
    setLoudnessLeveling(true);
    expect(clipGain(fakeBuf(0.1))).toBeCloseTo(0.92 / 0.1, 2); // quiet -> boosted toward target peak
    expect(clipGain(fakeBuf(0.92))).toBeCloseTo(1, 2); // already at target -> ~1x
    expect(clipGain(fakeBuf(0.00001))).toBe(1); // essentially silent -> no amplification
    expect(clipGain(fakeBuf(0.01))).toBeLessThanOrEqual(18); // boost capped
    setLoudnessLeveling(false);
    expect(clipGain(fakeBuf(0.1))).toBe(1); // disabled -> passthrough
    setLoudnessLeveling(true);
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
