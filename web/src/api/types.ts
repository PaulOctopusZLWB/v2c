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
  // Resolved global person (voiceprint identity) for this segment; null when unattributed.
  // Lets 审核 group/display by the re-identifiable person rather than the diarizer's spk_NN.
  person_id: string | null;
  person_label: string | null;
}

export interface TranscriptSession {
  session_id: string;
  /** User-given session name (rename dialog); null when unset. */
  name?: string | null;
  review_status: ReviewStatus | "blocked";
  segments: TranscriptSegment[];
}

/* ---- AI 预审 (rule-based triage, GET /api/sessions/{id}/triage) ---- */

export interface TriageReason {
  /** Machine-matchable kind: low_confidence | speaker_doubt | hallucination | context_break. */
  kind: string;
  /** Display-ready Chinese pill copy, e.g. 「置信 0.41」「说话人存疑 → 可能是 李四」. */
  label: string;
}

export interface TriageSegment {
  segment_id: string;
  /** high = 高置信可折叠批量接受;suspect = 可疑前置;manual = 正常人工审。 */
  bin: "high" | "suspect" | "manual";
  reasons: TriageReason[];
  confidence: number | null;
  review_status: ReviewStatus;
  /** 预留:ASR 备选/LLM 纠错文本;存在时审核页出现「采纳 AI 修正」。 */
  suggested_text: string | null;
  suggested_speaker: { person_id: string; person_label: string } | null;
}

export interface SessionTriage {
  session_id: string;
  thresholds: { high: number; low: number };
  summary: {
    total: number;
    bins: { high: number; suspect: number; manual: number };
    pending_high: number;
    pending_suspect: number;
    pending_manual: number;
    reasons: Record<string, number>;
  };
  segments: TriageSegment[];
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

export type ParticipantStatus = "present" | "absent" | "uncertain";

export interface IdentityParticipant {
  person_id: string;
  display_name: string;
  status: ParticipantStatus;
}

export interface IdentityCandidate {
  person_id?: string | null;
  display_name?: string | null;
  speaker?: string | null;
  status: "trusted" | "suggested" | "excluded" | "unknown" | "noise";
  safe_label: string;
  segment_count: number;
  segment_ids: string[];
  sample_text?: string | null;
  evidence_sources?: string[];
}

export interface IdentityReview {
  session_id: string;
  can_summarize: boolean;
  participants: IdentityParticipant[];
  candidates: IdentityCandidate[];
  new_person_candidates: IdentityCandidate[];
  mixed_clusters?: unknown[];
  excluded_people?: IdentityCandidate[];
  negative_feedback_count: number;
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
  /** User-chosen session name, or null when unnamed (then show the time label). */
  name?: string | null;
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
export interface SpeakerClusterSample {
  segment_id: string;
  text: string;
}

export interface SpeakerCluster {
  speaker_cluster_id: string;
  person_id: string | null;
  person_label: string | null;
  segment_count: number;
  total_speech_ms: number;
  sample_segment_id: string;
  sample_text: string;
  /** Global cluster list only: representative segments used as assignment evidence. */
  sample_segments?: SpeakerClusterSample[];
  /** Global cluster list only: how many of the cluster's segments are manually labeled. */
  labeled_count?: number;
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

/** One speaker's acoustic-emotion breakdown within a scope. */
export interface EmotionSpeaker {
  /** Resolved attribution label (person_label override, else raw speaker). */
  label: string;
  /** Total in-scope segments with an emotion for this speaker. */
  total: number;
  /** {emotion_label: count} over this speaker's segments. */
  emotions: Record<string, number>;
  /** Most frequent emotion label for this speaker. */
  dominant: string;
}

/** Per-segment acoustic-emotion distribution over a session/day scope. */
export interface EmotionDistribution {
  /** {emotion_label: count} across all in-scope segments by dominant label. */
  overall: Record<string, number>;
  /** Per-speaker emotion profiles, sorted by total desc. */
  per_speaker: EmotionSpeaker[];
  /** Total in-scope segments that have an emotion. */
  n: number;
}

/** {segment_id: dominant_emotion_label} for the map's color-by-emotion mode. */
export interface EmotionLabels {
  labels: Record<string, string>;
}

/** Outcome of a CAM++ similarity re-cluster pass driven by labeled anchors. */
export interface ReclusterResult {
  assigned: number;
  unassigned: number;
  total: number;
  per_person: Record<string, number>;
  threshold: number;
}

export type ProjectionMethod = "umap" | "pca" | "tsne";

/** One voiceprint projected to 2D (x/y in [0,1]) for the scatter "voiceprint map". */
export interface ProjectionPoint {
  segment_id: string;
  x: number;
  y: number;
  speaker: string | null;
  person_id: string | null;
  person_label: string | null;
  text: string | null;
  /** Originating session — lets the multi-scope map color/compare by session. */
  session_id: string | null;
}

/** Result of the embedding-projection endpoint: 2D points plus the method actually used. */
export interface ProjectionResult {
  points: ProjectionPoint[];
  /** The method actually used (umap/tsne fall back to pca below their min points or on failure). */
  method: ProjectionMethod;
  n: number;
  /** True when the in-scope set was evenly subsampled down to max_points to stay responsive. */
  capped?: boolean;
  /** Total in-scope segments before any subsampling. */
  total_in_scope?: number;
}

/** Multi-scope, tunable projection request: project a union of sessions + days together. */
export interface ProjectionRequest {
  session_ids?: string[];
  days?: string[];
  method?: ProjectionMethod;
  /** UMAP: neighborhood size. */
  n_neighbors?: number;
  /** UMAP: minimum embedded distance. */
  min_dist?: number;
  /** PCA: which principal components to plot on x / y. */
  pca_x?: number;
  pca_y?: number;
  /** t-SNE: perplexity. */
  perplexity?: number;
  /** Evenly subsample the scope down to this many points before projecting. */
  max_points?: number;
}

/** A person enriched with enrollment + attribution state (People panel). */
export interface PersonRow {
  person_id: string;
  display_name: string;
  /** 'contact' | 'self' | 'non_speaker' (噪音/多人) — render non_speaker specially / exclude from speaker analytics. */
  person_type: string;
  is_self: number;
  /** Has a stored voiceprint centroid (person_voiceprints row). */
  enrolled: boolean;
  /** Number of segment_person_overrides rows attributed to this person (manual + voiceprint). */
  attributed_count: number;
  /** Confirmed ground-truth labels (source='manual') — the enroll-able segment count. */
  manual_count: number;
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

/** One row in the 首页 (home) "最近会话" list: enough to deep-link into 审核. */
export interface HomeRecentSession {
  session_id: string;
  /** sessions.date_key — the day this session belongs to. */
  day: string;
  started_at: string;
  /** User-given session name (rename dialog); null → fall back to a time label. */
  name?: string | null;
  segment_count: number;
  /** Segments still pending review in this session(状态列「待审 N」). */
  pending_segments?: number;
  /** Confirmed-present participants joined with ' · '(身份审核);null when none. */
  participants?: string | null;
  review_status: ReviewStatus | "blocked";
}

/** The 首页 (home/landing) dashboard payload (`/api/home/overview`). */
export interface HomeOverview {
  /** Review backlog headline: sessions still needing review + total pending segments. */
  review: { pending_sessions: number; pending_segments: number };
  /** Person roster size + how many have an enrolled voiceprint. */
  people: { total: number; enrolled: number };
  /** Corpus coverage: distinct days, sessions, active segments, embedded + emoted counts. */
  coverage: { days: number; sessions: number; segments: number; embedded: number; emoted: number };
  /** The 5 most recent sessions, newest first. */
  recent_sessions: HomeRecentSession[];
  /** The most recent day string (for deep-linking 观点), or null when empty. */
  latest_day: string | null;
  /** 今日标题行:今天的段数与语音时长(「已录 n 段 · 时长」)。 */
  today?: { day: string; segments: number; speech_ms: number };
}

/** One cited claim/viewpoint with the transcript segment ids that back it. Refs are preserved
 *  verbatim through edits (the backend validates refs ⊆ the session's segment ids). */
export interface ViewpointClaim {
  text: string;
  evidence_refs: string[];
}

/** One to-do extracted from the session: a claim plus an owner. */
export interface ViewpointTodo {
  text: string;
  owner: string;
  evidence_refs: string[];
}

/** One speaker cluster's distilled stance within the session. `speaker_cluster_id` is preserved
 *  verbatim (the backend validates it ∈ the session's cluster labels). */
export interface ViewpointSpeaker {
  speaker_cluster_id: string;
  viewpoints: ViewpointClaim[];
  sentiment: string;
  stance: string;
  latent_needs: string[];
}

/** session_summary.v1 — the editable 观点 document for one session. */
export interface ViewpointContent {
  headline: string;
  summary: string;
  topics: string[];
  decisions: ViewpointClaim[];
  todos: ViewpointTodo[];
  open_questions: string[];
  core_conclusions: string[];
  per_speaker: ViewpointSpeaker[];
}

/** One transcript turn as surfaced by the viewpoint workspace (editable text + resolved speaker). */
export interface ViewpointSegment {
  segment_id: string;
  text: string;
  speaker: string;
  person_label: string | null;
}

/** The effective/default 观点 prompt for a session (per-session override over the global default). */
export interface ViewpointPrompt {
  effective: string;
  default: string;
  is_override: boolean;
}

/** The full per-session 观点 workspace state (`GET /api/sessions/{id}/viewpoint`). */
export interface ViewpointState {
  session_id: string;
  segments: ViewpointSegment[];
  prompt: ViewpointPrompt;
  identity_review?: Pick<IdentityReview, "can_summarize" | "participants" | "negative_feedback_count">;
  generated: ViewpointContent | null;
  edited: ViewpointContent | null;
  effective: ViewpointContent | null;
  status: "draft" | "edited" | "published";
  stale: boolean;
  has_generated: boolean;
  generating: boolean;
  published_at: string | null;
  note_path: string | null;
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
