import { useSyncExternalStore } from "react";
import { api } from "../api/client";

// One clip plays at a time, app-wide. useSegmentAudio() used to be per-component (each caller got
// its own AudioContext and never stopped the previous source), so plays from different turns / the
// voiceprint map stacked up and overlapped. This module-level singleton makes playback exclusive:
// starting any clip immediately stops whatever was playing, and every component shares one `playing`.
const listeners = new Set<() => void>();
let playingId: string | null = null;
let ctx: AudioContext | null = null;
let currentSource: AudioBufferSourceNode | null = null;
let currentEl: HTMLAudioElement | null = null;
let currentUrl: string | null = null;

// Loudness leveling: recordings vary wildly (one speaker loud, another faint). When on, each clip
// is peak-normalized to TARGET_PEAK so every utterance is audible without clipping the loud ones.
// Boost is capped so near-silence isn't amplified into noise. Persisted, default on.
const TARGET_PEAK = 0.92;
const MAX_GAIN = 18;
const LEVELING_KEY = "pcn.audio.leveling";
let leveling = readLeveling();
function readLeveling(): boolean {
  try {
    return localStorage.getItem(LEVELING_KEY) !== "off";
  } catch {
    return true;
  }
}
export function setLoudnessLeveling(on: boolean): void {
  leveling = on;
  try {
    localStorage.setItem(LEVELING_KEY, on ? "on" : "off");
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}
export function clipGain(buf: Pick<AudioBuffer, "numberOfChannels" | "getChannelData">): number {
  if (!leveling) return 1;
  let peak = 0;
  for (let ch = 0; ch < buf.numberOfChannels; ch++) {
    const data = buf.getChannelData(ch);
    // Stride large buffers — sampling the peak is plenty and keeps this O(1)-ish per clip.
    const step = Math.max(1, Math.floor(data.length / 8000));
    for (let i = 0; i < data.length; i += step) {
      const a = Math.abs(data[i]);
      if (a > peak) peak = a;
    }
  }
  if (peak < 1e-4) return 1; // essentially silent — don't amplify noise
  return Math.min(MAX_GAIN, TARGET_PEAK / peak);
}
// Bumped on every play()/stop(); a stale clip's onended/onplay callback checks its token against
// this and no-ops if it has been superseded (so stopping the old clip never clears the new one).
let token = 0;

function setPlaying(id: string | null): void {
  if (id === playingId) return;
  playingId = id;
  listeners.forEach((l) => l());
}

function teardown(): void {
  if (currentSource) {
    currentSource.onended = null;
    try {
      currentSource.stop();
    } catch {
      /* already stopped */
    }
    try {
      currentSource.disconnect();
    } catch {
      /* noop */
    }
    currentSource = null;
  }
  if (currentEl) {
    try {
      currentEl.pause();
    } catch {
      /* noop */
    }
    currentEl = null;
  }
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl);
    currentUrl = null;
  }
}

export function stopAudio(): void {
  token++;
  teardown();
  setPlaying(null);
}

async function play(segmentId: string): Promise<number[]> {
  // Exclusive: stop the current clip and claim "playing" immediately, before the fetch, so the new
  // segment highlights and the old one un-highlights without waiting on the network.
  const my = ++token;
  teardown();
  setPlaying(segmentId);

  let response: Response;
  try {
    response = await fetch(api.audioUrl(segmentId));
    if (!response.ok) throw new Error(`audio request failed: ${response.status}`);
  } catch (err) {
    if (my === token) setPlaying(null);
    throw err;
  }

  const Ctx =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) {
    // No Web Audio API: play the already-fetched clip via an <audio> element.
    const blob = await response.blob();
    if (my !== token) return []; // a newer play() superseded us while fetching
    const url = URL.createObjectURL(blob);
    const el = new Audio(url);
    currentEl = el;
    currentUrl = url;
    el.addEventListener("ended", () => {
      if (my === token) {
        teardown();
        setPlaying(null);
      }
    });
    try {
      await el.play();
    } catch (err) {
      if (my === token) {
        teardown();
        setPlaying(null);
      }
      throw err;
    }
    return [];
  }

  ctx ??= new Ctx();
  const decoded = await ctx.decodeAudioData(await response.arrayBuffer());
  if (my !== token) return []; // superseded while decoding
  const src = ctx.createBufferSource();
  src.buffer = decoded;
  // Peak-normalize via a gain node so quiet speakers are audible (loudness leveling).
  const gain = ctx.createGain();
  gain.gain.value = clipGain(decoded);
  src.connect(gain).connect(ctx.destination);
  currentSource = src;
  src.onended = () => {
    if (my === token) {
      currentSource = null;
      setPlaying(null);
    }
  };
  src.start();
  return peaks(decoded, 32);
}

export function useSegmentAudio() {
  const subscribe = (cb: () => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  };
  const playing = useSyncExternalStore(subscribe, () => playingId, () => null);
  const levelingOn = useSyncExternalStore(subscribe, () => leveling, () => true);
  return { play, stop: stopAudio, playing, leveling: levelingOn, setLeveling: setLoudnessLeveling };
}

/** Reset the shared audio state — for tests, so module state doesn't leak between cases. */
export function __resetAudioForTests(): void {
  token++;
  teardown();
  playingId = null;
  ctx = null;
}

function peaks(buf: AudioBuffer, n: number): number[] {
  const data = buf.getChannelData(0);
  const block = Math.floor(data.length / n) || 1;
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    let max = 0;
    for (let j = i * block; j < (i + 1) * block && j < data.length; j++) max = Math.max(max, Math.abs(data[j]));
    out.push(max);
  }
  return out;
}
