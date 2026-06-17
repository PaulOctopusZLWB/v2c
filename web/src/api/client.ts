import type { AutoAttributeResult, DailyLlmResult, DayStatusRow, EmbeddingStatus, EnrollResult, Health, LabelSegment, Person, PersonRow, ProjectionResult, ReclusterResult, ReviewStatus, SearchResult, Settings, SpeakerCluster, Suggestion, TaskRow, TranscriptSession } from "./types";

/** Build a `?a=1&b=2` query string, dropping null/undefined values. */
function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} failed: ${response.status}`);
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
  // transcript navigation + review
  days: () => request<{ days: Array<{ day: string; session_count: number }> }>("/api/transcripts/days"),
  dayStatus: () => request<{ days: DayStatusRow[] }>("/api/transcripts/day-status"),
  sessionsForDay: (day: string) =>
    request<{ day: string; sessions: Array<{ session_id: string; started_at: string; segment_count: number; review_status: string }> }>(`/api/transcripts/days/${day}/sessions`),
  session: (id: string) => request<TranscriptSession>(`/api/transcripts/sessions/${id}`),
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
  createPerson: (display_name: string) =>
    request<Person>("/api/persons", { method: "POST", body: JSON.stringify({ display_name }) }),
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
  recluster: (body: { anchors: Record<string, string>; threshold: number; session_id?: string | null; day?: string | null }) =>
    request<ReclusterResult>("/api/speakers/recluster", { method: "POST", body: JSON.stringify(body) }),
  speakerSegments: (params: { session_id?: string | null; speaker?: string | null; limit?: number | null }) =>
    request<{ segments: LabelSegment[] }>(`/api/speakers/segments${query(params)}`),
  // 2D voiceprint map: project stored CAM++ embeddings to a scatter (UMAP default, PCA fallback)
  embeddingProjection: (params: { session_id?: string | null; day?: string | null; method?: "umap" | "pca" | null }) =>
    request<ProjectionResult>(`/api/speakers/embedding-projection${query(params)}`),
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
  // read-only llm
  dailyLlm: (day: string) => request<DailyLlmResult>(`/api/llm/days/${day}`),
  audioUrl: (segmentId: string) => `/api/audio/segments/${segmentId}`,
  devices: () => request<{ sources: import("./types").ImportSource[] }>("/api/devices"),
};
