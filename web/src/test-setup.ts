import "@testing-library/jest-dom";
import { vi } from "vitest";

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
