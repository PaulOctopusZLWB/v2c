import { describe, expect, it } from "vitest";
import { speakerColor } from "../lib/speakerColors";

describe("speakerColor", () => {
  it("is stable per speaker label and differs across speakers", () => {
    expect(speakerColor("spk_1")).toBe(speakerColor("spk_1"));
    expect(speakerColor("spk_1")).not.toBe(speakerColor("spk_2"));
  });
  it("maps the self speaker to a fixed color", () => {
    expect(speakerColor("self")).toBe("#22d3ee");
  });
});
