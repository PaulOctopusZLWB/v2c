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

/** One session in the global review queue (`/api/transcripts/review-queue`): everything the
 *  inbox needs to rank + render a row without a per-session fetch. */
export interface ReviewQueueItem {
  session_id: string;
  /** sessions.date_key — the day this session belongs to. */
  day: string;
  started_at: string;
  /** Active segments with no review row yet. */
  pending: number;
  /** Active segment count. */
  total: number;
  /** Distinct speaker count among active segments. */
  speakers: number;
  /** 1 if any of the session's reviews is 'needs_fix', else 0. */
  has_flag: number;
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

/** Acoustic-emotion (emotion2vec) extraction coverage for a session/day. */
export interface EmotionStatus {
  total: number;
  emoted: number;
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

/** A person enriched with enrollment + attribution state (People panel). */
export interface PersonRow {
  person_id: string;
  display_name: string;
  is_self: number;
  /** Has a stored voiceprint centroid (person_voiceprints row). */
  enrolled: boolean;
  /** Number of segment_person_overrides rows attributed to this person. */
  attributed_count: number;
}

/** Result of enrolling a person's voiceprint (centroid summary). */
export interface EnrollResult {
  person_id: string;
  n_segments: number;
  dim: number;
}

/** One nearest-enrolled-person suggestion for a speaker cluster in a session. */
export interface Suggestion {
  speaker: string;
  person_id: string;
  person_label: string;
  /** Cosine similarity of the cluster mean to the person's centroid, rounded 3dp. */
  score: number;
}

/** Outcome of auto-attributing in-scope segments to enrolled person centroids. */
export interface AutoAttributeResult {
  assigned: number;
  unassigned: number;
  total: number;
  per_person: Record<string, number>;
  threshold: number;
}

/** A candidate segment to label as an anchor (voiceprint flow). */
export interface LabelSegment {
  segment_id: string;
  text: string;
  speaker: string;
  absolute_start_at: string | null;
  has_embedding: boolean;
}

/** One global transcript-search hit: enough to render a snippet and jump to the utterance. */
export interface SearchResult {
  segment_id: string;
  session_id: string;
  day: string;
  speaker: string;
  text: string;
  absolute_start_at: string | null;
}

/** One speaker's share of a session's conversation dynamics. */
export interface DynamicsSpeaker {
  /** Resolved attribution label (person_label override, else raw speaker). */
  label: string;
  /** Total talk time in ms (sum of segment end-start). */
  talk_ms: number;
  /** talk_ms / total_ms, rounded 3dp. */
  talk_share: number;
  /** Number of turns (maximal same-label runs in time order). */
  turns: number;
  segment_count: number;
  avg_segment_ms: number;
}

/** One turn-taking transition between consecutive turns. */
export interface DynamicsTransition {
  from: string;
  to: string;
  count: number;
}

/** One merged turn on the conversation timeline; offsets are ms relative to the
 *  session's earliest absolute start (cross-file safe). */
export interface DynamicsTurn {
  label: string;
  start_ms_rel: number;
  end_ms_rel: number;
  segment_ids: string[];
}

/** Per-session conversation dynamics: talk-share, turn-taking, timeline. */
export interface SessionDynamics {
  session_id: string;
  total_ms: number;
  speakers: DynamicsSpeaker[];
  transitions: DynamicsTransition[];
  timeline: DynamicsTurn[];
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
