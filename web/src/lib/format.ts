// Human-readable label helpers — no raw ids / task types / status codes in the UI.
import type { ReviewStatus, TranscriptSegment } from "../api/types";

export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** "14:32" from an ISO timestamp; "" if unparseable. */
export function timeOfDay(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[1]}:${m[2]}` : "";
}

const TASK_STATUS_ZH: Record<string, string> = {
  pending: "待处理",
  pending_asr: "待转写",
  claimed: "已认领",
  running: "处理中",
  succeeded: "完成",
  failed: "失败",
  failed_retryable: "失败·可重试",
  failed_terminal: "失败",
};
export function taskStatusZh(status: string): string {
  return TASK_STATUS_ZH[status] ?? status;
}

const TASK_TYPE_ZH: Record<string, string> = {
  vad: "预处理",
  asr: "转写",
  session_derive: "会话归并",
  summarize_session: "会话摘要",
  daily_generate: "日报生成",
  obsidian_publish: "发布",
  archive: "归档",
};
export function taskTypeZh(type: string): string {
  return TASK_TYPE_ZH[type] ?? type;
}

const REVIEW_STATUS_ZH: Record<ReviewStatus | "blocked", string> = {
  pending_review: "待审",
  accepted: "接受",
  rejected: "拒绝",
  needs_fix: "存疑",
  blocked: "受阻",
};
export function reviewStatusZh(status: ReviewStatus | "blocked"): string {
  return REVIEW_STATUS_ZH[status] ?? status;
}

/** Session list label from the days/sessions endpoint row. */
export function sessionListLabel(s: { started_at: string; segment_count: number }): string {
  const t = timeOfDay(s.started_at);
  return `${t || "会话"} · ${s.segment_count}段`;
}

/** Open-session header derived from its segments: "14:32–14:48 · 12段 · 2人". */
export function sessionHeader(segments: TranscriptSegment[]): { time: string; segs: number; speakers: number } {
  const segs = segments.length;
  const speakers = new Set(segments.map((x) => x.speaker)).size;
  if (segs === 0) return { time: "", segs: 0, speakers: 0 };
  const first = clock(segments[0].start_ms);
  const last = clock(segments[segs - 1].end_ms);
  return { time: `${first}–${last}`, segs, speakers };
}

/** Day label: keep the date, add weekday for scanability. */
export function dayLabel(day: string): string {
  const wd = ["日", "一", "二", "三", "四", "五", "六"];
  const d = new Date(`${day}T00:00:00`);
  const w = Number.isNaN(d.getTime()) ? "" : ` 周${wd[d.getDay()]}`;
  return `${day}${w}`;
}
