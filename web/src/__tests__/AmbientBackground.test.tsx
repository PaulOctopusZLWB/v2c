import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AmbientBackground } from "../components/AmbientBackground";

describe("AmbientBackground", () => {
  it("is purely decorative: hidden from the accessibility tree", () => {
    const { container } = render(<AmbientBackground />);
    const root = container.firstElementChild;
    expect(root).toHaveClass("ambient");
    expect(root).toHaveAttribute("aria-hidden", "true");
  });

  it("renders the four layers: grid, 3 aurora blobs, particles, vignette", () => {
    const { container } = render(<AmbientBackground />);
    expect(container.querySelector(".ambient-grid")).not.toBeNull();
    expect(container.querySelectorAll(".ambient-aurora")).toHaveLength(3);
    expect(container.querySelectorAll(".ambient-particle")).toHaveLength(7);
    expect(container.querySelector(".ambient-vignette")).not.toBeNull();
  });

  it("staggers particles with per-particle duration/delay custom properties", () => {
    const { container } = render(<AmbientBackground />);
    const particles = Array.from(container.querySelectorAll<HTMLElement>(".ambient-particle"));
    for (const p of particles) {
      expect(p.style.getPropertyValue("--dur")).toMatch(/^\d+(\.\d+)?s$/);
      expect(p.style.getPropertyValue("--delay")).toMatch(/^\d+(\.\d+)?s$/);
    }
    // deterministic but varied — not every particle on the same cycle
    expect(new Set(particles.map((p) => p.style.getPropertyValue("--dur"))).size).toBeGreaterThan(1);
  });
});
