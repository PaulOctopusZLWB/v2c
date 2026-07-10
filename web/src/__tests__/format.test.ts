import { describe, expect, it } from "vitest";
import { humanizeDuration } from "../lib/format";

describe("humanizeDuration", () => {
  it("renders sub-second durations in ms", () => {
    expect(humanizeDuration(0)).toBe("0ms");
    expect(humanizeDuration(420)).toBe("420ms");
    expect(humanizeDuration(999)).toBe("999ms");
  });

  it("renders sub-minute durations in seconds with one decimal", () => {
    expect(humanizeDuration(1000)).toBe("1s");
    expect(humanizeDuration(3200)).toBe("3.2s");
    expect(humanizeDuration(59999)).toBe("60s");
  });

  it("renders minute+ durations as \"Xm YYs\"", () => {
    expect(humanizeDuration(60000)).toBe("1m 00s");
    expect(humanizeDuration(65000)).toBe("1m 05s");
    expect(humanizeDuration(125000)).toBe("2m 05s");
  });

  it("clamps negative/NaN input to 0", () => {
    expect(humanizeDuration(-50)).toBe("0ms");
    expect(humanizeDuration(NaN)).toBe("0ms");
  });
});
