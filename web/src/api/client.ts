import type { AutoAttributeResult, DailyLlmResult, DayStatusRow, EmbeddingStatus, EmotionDistribution, EmotionLabels, EmotionStatus, EnrollResult, Health, HomeOverview, LabelSegment, Person, PersonRow, ProjectionRequest, ProjectionResult, ReclusterResult, ReviewQueueItem, ReviewStatus, SearchResult, SessionDynamics, Settings, SpeakerCluster, Suggestion, TaskRow, TranscriptSession, ViewpointContent, ViewpointPrompt, ViewpointState } from "./types";

/** Build a `?a=1&b=2` query string, dropping null/undefined values. */
function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!response.ok) {
    // Surface the backend's validation message (FastAPI's `detail`, or a plain message) so
    // callers — e.g. the 观点 editor on a 400 — can show what's wrong instead of "failed: 400".
    let detail = "";
    try {
      const body = (await response.clone().json()) as { detail?: unknown; message?: unknown };
      const d = body?.detail ?? body?.message;
      if (typeof d === "string") detail = d;
      else if (Array.isArray(d)) detail = d.map((e) => (e as { msg?: string })?.msg ?? String(e)).join("; ");
    } catch {
      /* non-JSON error body — fall back to the status line */
    }
    throw new Error(detail || `${init?.method ?? "GET"} ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  // pipeline control
  importDir: (source_dir: string) =>
    request<{ imported_files: number; queued: boolean }>("/api/pipeline/import", { method: "POST", body: JSON.stringify({ source_dir }) }),
  run: () => request<{ worker_running: boolean }>("/api/pipeline/run", { method: "POST" }),
  stop: () => request<{ stop_requested: boolean }>("/api/pipeline/stop", { method: "POST" }),
  retry: (taskId: string) => request<{ task_id: string; status: string }>(`/api/pipeline/tasks/${taskId}/retry`, { method: "POST" }),
  retryFailed: () => request<{ retried: number }>("/api/pipeline/retry-failed", { method: "POST" }),
  // status
  statusTasks: () => request<{ tasks: TaskRow[] }>("/api/status/tasks"),
  health: () => request<Health>("/api/health"),
  // 首页 (home/landing) dashboard: review backlog, people, coverage, recent sessions
  homeOverview: () => request<HomeOverview>("/api/home/overview"),
  // transcript navigation + review
  days: () => request<{ days: Array<{ day: string; session_count: number }> }>("/api/transcripts/days"),
  dayStatus: () => request<{ days: DayStatusRow[] }>("/api/transcripts/day-status"),
  // global review queue: one ranked list of sessions still needing review, across every day
  reviewQueue: (limit?: number) =>
    request<{ queue: ReviewQueueItem[] }>(`/api/transcripts/review-queue${query({ limit })}`),
  sessionsForDay: (day: string) =>
    request<{ day: string; sessions: Array<{ session_id: string; started_at: string; segment_count: number; review_status: string; name?: string | null }> }>(`/api/transcripts/days/${day}/sessions`),
  // Name a session (empty string clears it) — surfaces in the 审核 list + 声纹 scope picker.
  renameSession: (id: string, name: string) =>
    request<{ session_id: string; name: string | null }>(`/api/transcripts/sessions/${id}/name`, { method: "PUT", body: JSON.stringify({ name }) }),
  // Delete a session and cascade-remove all its segments' rows.
  deleteSession: (id: string) =>
    request<{ deleted: boolean; segments: number }>(`/api/transcripts/sessions/${id}`, { method: "DELETE" }),
  session: (id: string) => request<TranscriptSession>(`/api/transcripts/sessions/${id}`),
  // conversation dynamics for a session: talk-share, turn-taking, timeline
  sessionDynamics: (id: string) => request<SessionDynamics>(`/api/sessions/${id}/dynamics`),
  // global transcript search: substring match across every day, newest utterance first
  search: (q: string, limit?: number) =>
    request<{ results: SearchResult[] }>(`/api/transcripts/search${query({ q, limit })}`),
  reviewSegment: (id: string, status: ReviewStatus, note = "") =>
    request(`/api/transcripts/segments/${id}/review`, { method: "POST", body: JSON.stringify({ status, note }) }),
  batchReview: (segment_ids: string[], status: ReviewStatus, note = "") =>
    request<{ updated: number }>("/api/transcripts/segments/batch-review", { method: "POST", body: JSON.stringify({ segment_ids, status, note }) }),
  clearReview: (segment_ids: string[]) =>
    request<{ cleared: number }>("/api/transcripts/segments/clear-review", { method: "POST", body: JSON.stringify({ segment_ids }) }),
  acceptRemaining: (sessionId: string) =>
    request<{ accepted: number }>(`/api/transcripts/sessions/${sessionId}/accept-remaining`, { method: "POST" }),
  // persons / speakers
  persons: () => request<{ persons: Person[] }>("/api/persons"),
  createPerson: (display_name: string, person_type?: string) =>
    request<Person>("/api/persons", {
      method: "POST",
      body: JSON.stringify(person_type ? { display_name, person_type } : { display_name }),
    }),
  // Remove an accidental duplicate person (cascades across every referencing table).
  deletePerson: (id: string) =>
    request<{ deleted: boolean }>(`/api/persons/${id}`, { method: "DELETE" }),
  // Merge a duplicate person into another without losing their labels.
  mergePeople: (fromId: string, intoId: string) =>
    request<{ moved: number }>("/api/people/merge", { method: "POST", body: JSON.stringify({ from_id: fromId, into_id: intoId }) }),
  assignPerson: (speaker: string, person_id: string) =>
    request(`/api/speakers/${speaker}/assign-person`, { method: "POST", body: JSON.stringify({ person_id }) }),
  overridePerson: (segmentId: string, person_id: string) =>
    request(`/api/transcripts/segments/${segmentId}/person-override`, { method: "POST", body: JSON.stringify({ person_id }) }),
  speakerClusters: (day: string) =>
    request<{ clusters: SpeakerCluster[] }>(`/api/speakers/clusters?day=${encodeURIComponent(day)}`),
  assignPersonBulk: (speakers: string[], person_id: string) =>
    request<{ assigned: number }>("/api/speakers/assign-person-bulk", { method: "POST", body: JSON.stringify({ speakers, person_id }) }),
  // voiceprint (CAM++) re-clustering: extract embeddings, label anchors, propagate by similarity
  embeddingStatus: (scope: { session_id?: string | null; day?: string | null }) =>
    request<EmbeddingStatus>(`/api/speakers/embedding-status${query(scope)}`),
  extractEmbeddings: (scope: { session_id?: string | null; day?: string | null }) =>
    request<{ started: boolean }>("/api/speakers/extract-embeddings", { method: "POST", body: JSON.stringify(scope) }),
  // acoustic emotion (emotion2vec): per-segment 8-class emotion over existing audio slices
  emotionStatus: (scope: { session_id?: string | null; day?: string | null }) =>
    request<EmotionStatus>(`/api/emotions/status${query(scope)}`),
  extractEmotions: (scope: { session_id?: string | null; day?: string | null }) =>
    request<{ started: boolean }>("/api/emotions/extract", { method: "POST", body: JSON.stringify(scope) }),
  // per-segment emotion aggregates: overall + per-speaker distribution, and the map's label map
  emotionDistribution: (scope: { session_id?: string | null; day?: string | null }) =>
    request<EmotionDistribution>(`/api/emotions/distribution${query(scope)}`),
  emotionLabels: (scope: { session_id?: string | null; day?: string | null }) =>
    request<EmotionLabels>(`/api/emotions/labels${query(scope)}`),
  recluster: (body: { anchors: Record<string, string>; threshold: number; session_id?: string | null; day?: string | null }) =>
    request<ReclusterResult>("/api/speakers/recluster", { method: "POST", body: JSON.stringify(body) }),
  speakerSegments: (params: { session_id?: string | null; speaker?: string | null; limit?: number | null }) =>
    request<{ segments: LabelSegment[] }>(`/api/speakers/segments${query(params)}`),
  // 2D voiceprint map: project stored CAM++ embeddings to a scatter (UMAP default, PCA fallback)
  embeddingProjection: (params: { session_id?: string | null; day?: string | null; method?: "umap" | "pca" | null }) =>
    request<ProjectionResult>(`/api/speakers/embedding-projection${query(params)}`),
  // Multi-scope, tunable voiceprint projection: project a union of sessions + days together
  // (UMAP / PCA-component selection / t-SNE) with a perf cap for cross-session comparison.
  projection: (body: ProjectionRequest) =>
    request<ProjectionResult>("/api/speakers/projection", { method: "POST", body: JSON.stringify(body) }),
  // "People taught once": enroll a voiceprint, then suggest/auto-attribute everywhere; plus the
  // lasso-to-label bulk primitive (label a set of segments as one person).
  people: () => request<{ people: PersonRow[] }>("/api/people"),
  labelSegments: (personId: string, segment_ids: string[]) =>
    request<{ labeled: number }>(`/api/people/${personId}/label-segments`, { method: "POST", body: JSON.stringify({ segment_ids }) }),
  enrollPerson: (personId: string, segment_ids?: string[]) =>
    request<EnrollResult>(`/api/people/${personId}/enroll`, { method: "POST", body: JSON.stringify({ segment_ids }) }),
  suggestPeople: (session_id: string) =>
    request<{ suggestions: Suggestion[] }>("/api/speakers/suggest", { method: "POST", body: JSON.stringify({ session_id }) }),
  autoAttribute: (params: { session_id?: string | null; day?: string | null; threshold?: number | null }) =>
    request<AutoAttributeResult>("/api/people/auto-attribute", { method: "POST", body: JSON.stringify(params) }),
  // settings (model/runtime overrides; take effect on the next run)
  settings: () => request<Settings>("/api/settings"),
  updateSettings: (body: Partial<Settings>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  // read-only llm (daily rollup)
  dailyLlm: (day: string) => request<DailyLlmResult>(`/api/llm/days/${day}`),
  // 观点 (per-session viewpoint workspace): the editable transcript/prompt/result single source
  // of truth. Every step is manual — edit segment text, (re)generate, edit the result, publish.
  viewpoint: (id: string) => request<ViewpointState>(`/api/sessions/${id}/viewpoint`),
  // Edit a transcript turn's text in place (makes the generated 观点 stale).
  editSegmentText: (id: string, text: string) =>
    request<{ segment_id: string; text: string }>(`/api/transcripts/segments/${id}`, { method: "PATCH", body: JSON.stringify({ text }) }),
  // Enqueue (re)generation of the session's 观点; poll viewpoint() until generating flips false.
  generateViewpoint: (id: string) =>
    request<{ enqueued: boolean; session_id: string }>(`/api/sessions/${id}/viewpoint/generate`, { method: "POST" }),
  // Save a hand-edited 观点 document; 400 (with a validation message) when refs/clusters are bad.
  editViewpoint: (id: string, content: ViewpointContent) =>
    request<ViewpointState>(`/api/sessions/${id}/viewpoint`, { method: "PUT", body: JSON.stringify({ content }) }),
  // Discard the manual edits, reverting to the generated baseline.
  clearViewpointEdit: (id: string) =>
    request<ViewpointState>(`/api/sessions/${id}/viewpoint/edit`, { method: "DELETE" }),
  // Confirm + write the effective 观点 to Obsidian; 409 when nothing has been generated yet.
  publishViewpoint: (id: string) =>
    request<{ note_path: string; published_at: string }>(`/api/sessions/${id}/viewpoint/publish`, { method: "POST" }),
  // The global 观点 prompt template + its built-in default.
  getSessionPrompt: () => request<{ template: string; default: string }>("/api/prompts/session_viewpoint"),
  setSessionPrompt: (template: string) =>
    request<{ template: string; default: string }>("/api/prompts/session_viewpoint", { method: "PUT", body: JSON.stringify({ template }) }),
  // Per-session prompt override (null clears it, falling back to the global default).
  setSessionPromptOverride: (id: string, template: string | null) =>
    request<ViewpointPrompt>(`/api/sessions/${id}/viewpoint/prompt`, { method: "PUT", body: JSON.stringify({ template }) }),
  audioUrl: (segmentId: string) => `/api/audio/segments/${segmentId}`,
  devices: () => request<{ sources: import("./types").ImportSource[] }>("/api/devices"),
};
