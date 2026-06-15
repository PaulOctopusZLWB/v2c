export type ReviewStatus = "pending_review" | "accepted" | "rejected" | "needs_fix";

export interface TranscriptSegment {
  segment_id: string;
  text: string;
  speaker: string;
  start_ms: number;
  end_ms: number;
  review_status: ReviewStatus;
  note: string | null;
}

export interface TranscriptSession {
  session_id: string;
  review_status: ReviewStatus | "blocked";
  segments: TranscriptSegment[];
}

export interface TaskRow {
  task_id: string;
  task_type: string;
  target_type: string;
  target_id: string;
  status: string;
  attempt_count: number;
  last_error: string | null;
  duration_ms: number | null;
}

export interface Person {
  person_id: string;
  display_name: string;
  person_type: string;
  is_self: number;
}

export interface ImportProgress {
  active: boolean;
  done: number;
  total: number;
  current: string;
}

export interface StatusSnapshot {
  tasks: TaskRow[];
  worker_running: boolean;
  import_progress?: ImportProgress | null;
}

/**
 * Compact per-tick status pushed over SSE (`status.summary`) — replaces the old
 * full task array so the stream stays small at ~1881 tasks. Counts are keyed by
 * task status; `active_stage` is the task_type of the in-flight task.
 */
export interface StatusSummary {
  status_counts: Record<string, number>;
  total: number;
  stage_counts?: Record<string, { done: number; total: number }>;
  eta_seconds?: number | null;
  active_stage: string | null;
  current_target: string | null;
  import_progress?: ImportProgress | null;
  worker_running: boolean;
}

/** Per-day processing/ready aggregate from `/api/transcripts/day-status`. */
export interface DayStatusRow {
  day: string;
  session_count: number;
  active_count: number;
  total_count: number;
  status: "processing" | "ready";
}

export interface Health {
  require_accepted_transcripts: boolean;
}

export interface ImportSource {
  kind: "device" | "known";
  device_id: string;
  label: string;
  root_path: string;
  audio_count: number;
}

export interface DailyLlmResult {
  day: string;
  context: { content: Record<string, unknown>; model_name: string | null; updated_at: string } | null;
  memory_candidates: Array<{
    candidate_id: string;
    candidate_claim: string;
    edited_claim: string | null;
    claim_type: string;
    confidence: number;
    status: string;
    evidence_segment_ids: string[];
  }>;
}
