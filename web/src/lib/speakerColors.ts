/* Design-handoff speaker palette — same values in BOTH themes (theme.css --spk-*);
   chip text is always --chip-fg dark ink, so every fill here must stay mid-bright. */
const PALETTE = ["#5fb8d4", "#d4a75f", "#b48ce0", "#8b7ae8"];

export function speakerColor(speaker: string): string {
  if (speaker === "self" || speaker === "我") return "#7fd1a8"; // 我 (--spk-self)
  let hash = 0;
  for (let i = 0; i < speaker.length; i++) hash = (hash * 31 + speaker.charCodeAt(i)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}
