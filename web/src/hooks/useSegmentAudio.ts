import { useRef, useState } from "react";
import { api } from "../api/client";

export function useSegmentAudio() {
  const ctxRef = useRef<AudioContext | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);

  async function play(segmentId: string): Promise<number[]> {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) {
      new Audio(api.audioUrl(segmentId)).play().catch(() => undefined);
      return [];
    }
    const ctx = (ctxRef.current ??= new Ctx());
    const buf = await fetch(api.audioUrl(segmentId)).then((r) => r.arrayBuffer());
    const audio = await ctx.decodeAudioData(buf);
    const src = ctx.createBufferSource();
    src.buffer = audio;
    src.connect(ctx.destination);
    setPlaying(segmentId);
    src.onended = () => setPlaying((p) => (p === segmentId ? null : p));
    src.start();
    return peaks(audio, 32);
  }

  return { play, playing };
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
