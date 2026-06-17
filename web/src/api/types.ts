export type ReviewStatus = "pending_review" | "accepted" | "rejected" | "needs_fix";

export interface TranscriptSegment {
  segment_id: string;
  text: string;
  speaker: string;
  start_ms: number;
  end_ms: number;
  // Absolute wall-clock timeline (a session can fan in many files, so start_ms is per-file
  // and not comparable across them). May be null/"" for legacy chunk-mode rows.
  absolute_start_at: string | null;
  absolute_end_at: string | null;
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
  /** Tasks that will not progress on their own (succeeded + terminal/exhausted failures). */
  done_total?: number;
  /** Subset of done_total that ended in failure (terminal, or retryable with retries exhausted). */
  failed_total?: number;
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

export type AsrMode = "chunk" | "diarize";

/** Web-settable model/runtime overrides. Effective current values (overrides merged over
 *  env/config defaults). `GLM_API_KEY` is intentionally NOT here — it stays env-managed. */
export interface Settings {
  asr_mode: AsrMode;
  asr_preset_spk_num: number | null;
  glm_model: string;
  glm_base_url: string;
  glm_thinking: boolean;
}

/** One diarization cluster (`spk_NN`) for a day, with its current person mapping + a sample. */
export interface SpeakerCluster {
  speaker_cluster_id: string;
  person_id: string | null;
  person_label: string | null;
  segment_count: number;
  total_speech_ms: number;
  sample_segment_id: string;
  sample_text: string;
}

/** Embedding (voiceprint) extraction coverage for a session/day. */
export interface EmbeddingStatus {
  total: number;
  embedded: number;
  pending: number;
}

/** Outcome of a CAM++ similarity re-cluster pass driven by labeled anchors. */
export interface ReclusterResult {
  assigned: number;
  unassigned: number;
  total: number;
  per_person: Record<string, number>;
  threshold: number;
}

/** One voiceprint projected to 2D (x/y in [0,1]) for the scatter "voiceprint map". */
export interface ProjectionPoint {
  segment_id: string;
  x: number;
  y: number;
  speaker: string | null;
  person_id: string | null;
  person_label: string | null;
  text: string | null;
}

/** Result of the embedding-projection endpoint: 2D points plus the method actually used. */
export interface ProjectionResult {
  points: ProjectionPoint[];
  method: "umap" | "pca";
  n: number;
}

/** A candidate segment to label as an anchor (voiceprint flow). */
export interface LabelSegment {
  segment_id: string;
  text: string;
  speaker: string;
  absolute_start_at: string | null;
  has_embedding: boolean;
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
