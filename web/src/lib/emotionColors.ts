// Fixed colour + short-label + emoji for each acoustic-emotion class (emotion2vec 8-class +
// "其他/other" + "<unk>"). Labels arrive as "中文/english" (e.g. "开心/happy"); we key off the
// english half so a relabel of the zh side (or a bare english/zh label) still resolves.

interface EmotionMeta {
  color: string;
  /** Short zh label for chips/legends. */
  zh: string;
  emoji: string;
}

const MUTED = "#8b8d98";

/** Keyed by the english class name (lower-case). */
const META: Record<string, EmotionMeta> = {
  angry: { color: "#e5484d", zh: "生气", emoji: "😠" },
  disgusted: { color: "#30a46c", zh: "厌恶", emoji: "🤢" },
  fearful: { color: "#8b5cf6", zh: "恐惧", emoji: "😨" },
  happy: { color: "#f5d90a", zh: "开心", emoji: "🙂" },
  neutral: { color: MUTED, zh: "中立", emoji: "😐" },
  other: { color: MUTED, zh: "其他", emoji: "🫥" },
  sad: { color: "#3b82f6", zh: "难过", emoji: "😢" },
  surprised: { color: "#f76808", zh: "吃惊", emoji: "😮" }
};

const UNKNOWN: EmotionMeta = { color: MUTED, zh: "未知", emoji: "❔" };

/** The english key of a "中文/english" (or bare) label, lower-cased. */
function key(label: string): string {
  const parts = String(label ?? "").split("/");
  return (parts[parts.length - 1] || "").trim().toLowerCase();
}

/** Full metadata (colour + zh short label + emoji) for an emotion label. */
export function emotionMeta(label: string): EmotionMeta {
  return META[key(label)] ?? UNKNOWN;
}

/** The fixed colour for an emotion label (muted for other/unknown). */
export function emotionColor(label: string): string {
  return emotionMeta(label).color;
}
