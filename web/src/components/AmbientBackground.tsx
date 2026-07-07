import type { CSSProperties } from "react";

/* 全局环境动效层 (design handoff Phase 0) — fixed, pointer-transparent, at --z-ambient
 * behind every screen. Layers: 48px grid → 3 drifting aurora blobs → ~7 floating
 * particles (dark theme only) → vignette back to pure --bg. All motion is
 * transform/opacity-only and degrades to static gradients under
 * prefers-reduced-motion (see the AMBIENT section of styles.css). */

/* Fixed particle set (deterministic — no per-mount randomness, so React re-renders
 * never restart an animation mid-float). size 2–3px, floatup 9–15s, staggered delays. */
const PARTICLES: Array<{ left: string; size: number; dur: number; delay: number }> = [
  { left: "8%", size: 2, dur: 11, delay: 0 },
  { left: "22%", size: 3, dur: 14, delay: 2.5 },
  { left: "37%", size: 2, dur: 9, delay: 5 },
  { left: "52%", size: 2, dur: 13, delay: 1.5 },
  { left: "66%", size: 3, dur: 15, delay: 7 },
  { left: "79%", size: 2, dur: 10, delay: 4 },
  { left: "91%", size: 3, dur: 12, delay: 8.5 },
];

export function AmbientBackground() {
  return (
    <div className="ambient" aria-hidden="true">
      <div className="ambient-grid" />
      <div className="ambient-aurora ambient-aurora--a" />
      <div className="ambient-aurora ambient-aurora--b" />
      <div className="ambient-aurora ambient-aurora--c" />
      <div className="ambient-particles">
        {PARTICLES.map((p, i) => (
          <span
            key={i}
            className="ambient-particle"
            style={
              {
                left: p.left,
                width: p.size,
                height: p.size,
                "--dur": `${p.dur}s`,
                "--delay": `${p.delay}s`,
              } as CSSProperties
            }
          />
        ))}
      </div>
      <div className="ambient-vignette" />
    </div>
  );
}
