const PALETTE = ["#34d399", "#fbbf24", "#a78bfa", "#f472b6", "#60a5fa", "#fb923c", "#4ade80"];

export function speakerColor(speaker: string): string {
  if (speaker === "self" || speaker === "我") return "#22d3ee"; // self = accent
  let hash = 0;
  for (let i = 0; i < speaker.length; i++) hash = (hash * 31 + speaker.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}
