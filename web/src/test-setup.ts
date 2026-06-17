import "@testing-library/jest-dom";
import { beforeEach, vi } from "vitest";
import { __resetAudioForTests } from "./hooks/useSegmentAudio";

// jsdom does not implement media playback; stub it so SegmentRow playback paths run
// without emitting "Not implemented: HTMLMediaElement.prototype.play" noise.
Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  value: vi.fn().mockResolvedValue(undefined)
});
Object.defineProperty(HTMLMediaElement.prototype, "pause", {
  configurable: true,
  value: vi.fn()
});
// jsdom does not implement object URLs; the audio fallback path needs them.
Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:mock") });
Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });

// The audio player is a module-level singleton; reset it between tests so `playing` state
// (and any in-flight clip) doesn't leak across cases.
beforeEach(() => __resetAudioForTests());
