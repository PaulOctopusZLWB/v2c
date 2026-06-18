import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api/client";
import { Progress } from "./components/Progress";
import { RunInspector } from "./components/RunInspector";
import { SettingsPanel } from "./components/SettingsPanel";
import { ClusterPanel } from "./components/ClusterPanel";
import { TaskList } from "./components/TaskList";
import { Icon } from "./components/Icon";
import { Toasts, useToasts } from "./components/Toasts";
import { DevicePanel } from "./features/device/DevicePanel";
import { HomePanel } from "./features/home/HomePanel";
import { WorkspaceNav } from "./features/workspace/WorkspaceNav";
import { ReviewQueue } from "./features/transcript/ReviewQueue";
import { TranscriptReviewPanel } from "./features/transcript/TranscriptReviewPanel";
import { SpeakerPanel } from "./features/speakers/SpeakerPanel";
import { VoiceprintPanel } from "./features/speakers/VoiceprintPanel";
import { PeoplePanel } from "./features/people/PeoplePanel";
import { VoiceprintMap, type VoiceprintMapState } from "./features/viz/VoiceprintMap";
import { VoiceprintWorkflowPanel } from "./features/speakers/VoiceprintWorkflowPanel";
import { ScopeSelector, type Scope } from "./features/viz/ScopeSelector";
import { ProjectionControls, PROJ_DEFAULTS, type ProjParams } from "./features/viz/ProjectionControls";
import { DynamicsCharts } from "./features/viz/DynamicsCharts";
import { EmotionCharts } from "./features/viz/EmotionCharts";
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { ViewpointWorkspace } from "./features/viewpoint/ViewpointWorkspace";
import { Tabs } from "./features/workspace/Tabs";
import { ThemeToggle } from "./features/workspace/ThemeToggle";
import { useTab } from "./features/workspace/useTab";
import type { TabId } from "./features/workspace/useTab";
import { CommandPalette, type Command } from "./features/command/CommandPalette";
import { useHotkeys } from "./features/command/useHotkeys";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { stageForTaskType, STAGES } from "./lib/stages";
import type { Stage } from "./lib/stages";
import { dayLabel, taskTypeZh } from "./lib/format";
import { t } from "./i18n";
import type { DailyLlmResult, DayStatusRow, Health, ImportSource, Person, PersonRow, ProjectionRequest, ReviewStatus, SearchResult, TaskRow, TranscriptSession } from "./api/types";

const DEVICE_POLL_MS = 5000;
const ACTIVE_STATUSES = ["pending", "claimed", "running"];

/** Per-task_type done/total breakdown for the Progress bar, from the compact SSE summary
 *  (so the always-visible header shows it without fetching the full task list). */
function stageBreakdownFromSummary(
  stageCounts: Record<string, { done: number; total: number }> | undefined
): Array<{ label: string; done: number; total: number }> {
  if (!stageCounts) return [];
  // Only show stages that still have unfinished work, ordered by the pipeline DAG.
  const order = ["vad", "asr", "session_derive", "summarize_session", "daily_generate", "obsidian_publish", "archive"];
  return Object.entries(stageCounts)
    .filter(([, c]) => c.done < c.total)
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([type, c]) => ({ label: taskTypeZh(type), done: c.done, total: c.total }));
}

/** Build the multi-scope projection request from the 声纹 scope + tuning params, keeping only
 *  the params relevant to the chosen method. Returns null when nothing is selected (→ the map's
 *  pick/empty state) so we never fire an empty projection. */
function buildRequest(scope: Scope, params: ProjParams): ProjectionRequest | null {
  if (scope.session_ids.length === 0 && scope.days.length === 0) return null;
  const method = params.method ?? "umap";
  const base: ProjectionRequest = { session_ids: scope.session_ids, days: scope.days, method };
  if (method === "umap") return { ...base, n_neighbors: params.n_neighbors, min_dist: params.min_dist };
  if (method === "pca") return { ...base, pca_x: params.pca_x, pca_y: params.pca_y };
  return { ...base, perplexity: params.perplexity };
}

/** Bold the (case-insensitive) first occurrence of `q` within `text` for a search snippet.
 *  Returns the plain string when q is empty or doesn't appear, so non-matches render unchanged. */
function highlightMatch(text: string, q: string): ReactNode {
  if (!q) return text;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <strong>{text.slice(idx, idx + q.length)}</strong>
      {text.slice(idx + q.length)}
    </>
  );
}

export function App() {
  const { summary, worker_running, import_progress } = usePipelineStatus();
  const { tab, setTab } = useTab();
  const { toasts, push, pushAction, dismiss } = useToasts();
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [dayStatus, setDayStatus] = useState<DayStatusRow[]>([]);
  // The full task list is fetched lazily (only when the TaskList panel opens) — the
  // per-tick SSE stream now carries a compact summary, not the ~1881-row array.
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  // The day the 声纹聚类 panel inspects: defaults to the currently-selected day, but the user
  // can pick any day via a date input when no day is selected from the rail.
  const [clusterDay, setClusterDay] = useState<string>("");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<TranscriptSession | null>(null);
  // 审核 tab left column: the global review queue (default) vs. the by-day browser. `queueVersion`
  // bumps after a review action so the queue refetches and a finished session leaves the list.
  const [reviewMode, setReviewMode] = useState<"queue" | "days">("queue");
  const [queueVersion, setQueueVersion] = useState(0);
  // Bumped after a session rename/delete so WorkspaceNav's local by-day session list refetches.
  const [sessionsVersion, setSessionsVersion] = useState(0);
  const [persons, setPersons] = useState<Person[]>([]);
  // "People taught once": the enriched person roster (enrollment + attribution) for the 声纹 tab,
  // plus a key that, when bumped, remounts the voiceprint map so it refetches recoloured points.
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [mapKey, setMapKey] = useState(0);
  // 声纹 projection — fully decoupled from 审核: a multi-day/session scope + tunable params, and
  // the applied request that the map actually fetches. Param edits update projParams but DON'T
  // refetch; method/scope changes auto-apply, while slider drags wait for the 投射 button.
  const [scope, setScope] = useState<Scope>({ session_ids: [], days: [] });
  const [projParams, setProjParams] = useState<ProjParams>({ ...PROJ_DEFAULTS });
  const [appliedRequest, setAppliedRequest] = useState<ProjectionRequest | null>(null);
  const [voiceprintProjectionState, setVoiceprintProjectionState] = useState<VoiceprintMapState>({ status: "idle" });
  const [voiceprintSelectedCount, setVoiceprintSelectedCount] = useState(0);
  const [lastAutoAttributeCount, setLastAutoAttributeCount] = useState<number | null>(null);
  // Last projection outcome (subsample note) reported by the map, surfaced in ProjectionControls.
  const [projCapped, setProjCapped] = useState<{ capped: boolean; n: number; total: number } | null>(null);
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);
  // 观点 tab view: the per-session editable workspace (default) vs. the legacy read-only
  // 日报汇总 (daily rollup) reusing LlmResultPanel.
  const [llmMode, setLlmMode] = useState<"session" | "daily">("session");
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<string | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);
  // ⌘K command palette (keyboard-driven launcher); closed by default.
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The 对话分析 charts use recharts ResponsiveContainer, which measures 0 width inside a
  // collapsed <details> (display:none) and never re-measures on expand — so mount them only
  // once the section is open, when the container has its real (full) width.
  const [analysisOpen, setAnalysisOpen] = useState(false);
  useHotkeys({ "mod+k": (e) => { e.preventDefault(); setPaletteOpen((v) => !v); } });
  // Global transcript search, driven from the palette: the typed query + its async results.
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  // Mirror bootstrap state into a ref so the mount-time poll interval reads the latest value
  // (its closure captures the initial state).
  const bootstrappedRef = useRef(false);
  useEffect(() => {
    bootstrappedRef.current = bootstrapped;
  }, [bootstrapped]);
  // Mirror tasksOpen into a ref so the mount-time poll closure re-reads the latest value.
  const tasksOpenRef = useRef(false);
  useEffect(() => {
    tasksOpenRef.current = tasksOpen;
  }, [tasksOpen]);

  // Wrap any async action so a rejected api call surfaces a dismissable error toast.
  function guard<A extends unknown[]>(fn: (...args: A) => Promise<unknown>) {
    return async (...args: A) => {
      try {
        await fn(...args);
      } catch (err) {
        push(t.error.title, err instanceof Error ? err.message : undefined);
      }
    };
  }

  async function refreshDevices() {
    try {
      setSources((await api.devices()).sources ?? []);
    } catch {
      /* keep last-known sources on transient errors */
    }
  }

  async function refreshDays() {
    // Fetch the day list and the per-day processing/ready aggregate together so the
    // rail can render a live badge during a run, not only on running -> idle.
    const [daysResult, statusResult] = await Promise.all([api.days(), api.dayStatus().catch(() => ({ days: [] }))]);
    setDays(daysResult.days ?? []);
    setDayStatus(statusResult.days ?? []);
  }

  // Reload the enriched person roster (enrollment + attribution counts) for the People panel.
  async function refreshPeople() {
    try {
      setPeople((await api.people()).people ?? []);
    } catch {
      /* keep last-known people on transient errors */
    }
  }

  // A teaching mutation (label/enroll/suggest/auto-attribute) landed: reload the People roster,
  // refresh the base persons list (a new person may exist), and remount the map so it refetches
  // the now-recoloured projection.
  function onPeopleChanged() {
    void refreshPeople();
    void api.persons().then((r) => setPersons(r.persons ?? [])).catch(() => undefined);
    setMapKey((k) => k + 1);
  }

  // Lazily load the full task list — only when the TaskList panel is open. The SSE
  // summary feeds counts/progress; the heavy per-row detail is fetched on demand.
  async function refreshTasks() {
    const result = await api.statusTasks();
    setTasks(result.tasks ?? []);
  }

  // Load the initial workspace state. If the backend/API is unreachable, surface an
  // actionable error + retry instead of silently rendering an empty workspace.
  async function refreshBootstrap() {
    setBootstrapError(null);
    try {
      const [personsResult, healthResult, daysResult, devicesResult, peopleResult] = await Promise.all([
        api.persons(),
        api.health(),
        api.days(),
        api.devices(),
        api.people().catch(() => ({ people: [] as PersonRow[] }))
      ]);
      setPersons(personsResult.persons ?? []);
      setHealth(healthResult);
      setDays(daysResult.days ?? []);
      setSources(devicesResult.sources ?? []);
      setPeople(peopleResult.people ?? []);
      setBootstrapped(true);
    } catch (err) {
      setBootstrapError(err instanceof Error ? err.message : "API bootstrap failed");
    }
  }

  useEffect(() => {
    void refreshBootstrap();
    const timer = setInterval(() => {
      // Don't poll while the bootstrap-error screen is up: refreshBootstrap (重试) is the
      // sole recovery path, so the rail can't fill with days while the center stays errored.
      if (!bootstrappedRef.current) return;
      void refreshDevices();
      // Backstop the running -> idle day refresh: if that single SSE transition is missed
      // (dropped/coalesced frame), the poll still surfaces a freshly produced day. This also
      // refreshes the per-day processing/ready badge live during a run.
      void refreshDays().catch(() => undefined);
      // Keep the lazily-fetched task list fresh while its panel is open.
      if (tasksOpenRef.current) void refreshTasks().catch(() => undefined);
    }, DEVICE_POLL_MS);
    return () => clearInterval(timer);
  }, []);

  async function handleImport(root: string) {
    if (!root) return;
    await api.importDir(root);
    await api.run(); // import only enqueues; explicitly start the background worker.
    // The new day appears once the run finishes (the running -> idle effect calls
    // refreshDays): import is async and days derive from sessions that exist only after
    // the pipeline drains, so refreshing here would just re-fetch the same empty list.
  }

  async function selectDay(day: string) {
    setSelectedDay(day);
    setClusterDay(day); // keep the 声纹聚类 panel in sync with the rail selection
    setSelectedSessionId(null);
    setSession(null);
    setHighlightedSegmentId(null);
    try {
      setLlm(await api.dailyLlm(day));
    } catch {
      setLlm(null);
    }
  }

  async function selectSession(id: string) {
    setSelectedSessionId(id);
    setHighlightedSegmentId(null);
    setSession(await api.session(id));
  }

  // Debounced global transcript search: while the palette is open and the query is >=2 chars,
  // hit /api/transcripts/search ~200ms after the last keystroke; shorter/blank queries clear the
  // results. Each run is guarded by a stale flag so an out-of-order response can't overwrite a
  // newer query's results.
  useEffect(() => {
    const q = searchQuery.trim();
    if (!paletteOpen || q.length < 2) {
      setSearchResults([]);
      return;
    }
    let stale = false;
    const timer = setTimeout(() => {
      void api
        .search(q, 30)
        .then((r) => { if (!stale) setSearchResults(r.results ?? []); })
        .catch(() => { if (!stale) setSearchResults([]); });
    }, 200);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [searchQuery, paletteOpen]);

  // Jump from a search hit straight to its utterance: load+select its session (so the 审核 panel
  // has data), switch to that tab, highlight the segment (the existing `.hl`/scroll path makes it
  // visible), and best-effort play its audio. Audio playback failures are swallowed — the jump +
  // highlight is the contract.
  async function jumpToSegment(segment_id: string, session_id: string) {
    await selectSession(session_id);
    setTab("review");
    setHighlightedSegmentId(segment_id);
    try {
      await new Audio(api.audioUrl(segment_id)).play();
    } catch {
      /* best-effort: highlight + scroll is enough */
    }
  }

  async function reloadSession() {
    if (selectedSessionId) setSession(await api.session(selectedSessionId));
  }

  // Name a session (empty clears it), then refetch the by-day session list so the new name shows.
  async function handleRenameSession(sessionId: string, name: string) {
    await api.renameSession(sessionId, name);
    setSessionsVersion((v) => v + 1);
  }

  // Delete a session (cascades server-side), then refresh the day + session lists and the review
  // queue; if the deleted session was open, clear the selection so the panel doesn't show stale data.
  async function handleDeleteSession(sessionId: string) {
    await api.deleteSession(sessionId);
    if (sessionId === selectedSessionId) {
      setSelectedSessionId(null);
      setSession(null);
      setHighlightedSegmentId(null);
    }
    setSessionsVersion((v) => v + 1);
    setQueueVersion((v) => v + 1);
    await refreshDays();
  }

  // Patch the given segments' review_status in local session state, leaving everything else
  // untouched. Pure functional update so React sees a new object and TurnBlock re-renders.
  function patchSegmentStatuses(updates: Map<string, ReviewStatus>) {
    setSession((prev) =>
      prev
        ? {
            ...prev,
            segments: prev.segments.map((seg) =>
              updates.has(seg.segment_id) ? { ...seg, review_status: updates.get(seg.segment_id)! } : seg
            )
          }
        : prev
    );
  }

  // Restore each segment to a remembered prior status: those that were 'pending_review' get
  // their review row CLEARED (api.clearReview), the rest re-batched per their prior status.
  // Used by the Undo affordance on accept/reject/needs_fix.
  async function restorePriorStatuses(prior: Map<string, ReviewStatus>) {
    const byStatus = new Map<ReviewStatus, string[]>();
    for (const [id, status] of prior) {
      const bucket = byStatus.get(status) ?? [];
      bucket.push(id);
      byStatus.set(status, bucket);
    }
    for (const [status, ids] of byStatus) {
      if (status === "pending_review") await api.clearReview(ids);
      else await api.batchReview(ids, status);
    }
  }

  // OPTIMISTIC batch review: flip local state immediately (no refetch first), call the API,
  // then offer an Undo toast. On API failure, roll the local state back.
  async function handleBatchReview(segment_ids: string[], status: ReviewStatus) {
    if (!session || segment_ids.length === 0) return;
    // Snapshot each affected segment's PREVIOUS status so Undo / rollback can restore it.
    const byId = new Map(session.segments.map((s) => [s.segment_id, s.review_status as ReviewStatus]));
    const priorById = new Map<string, ReviewStatus>(
      segment_ids.map((id) => [id, byId.get(id) ?? "pending_review"])
    );

    // Update the UI instantly.
    patchSegmentStatuses(new Map(segment_ids.map((id) => [id, status])));

    try {
      await api.batchReview(segment_ids, status);
    } catch (err) {
      // Roll back to the captured previous statuses and surface the error.
      patchSegmentStatuses(priorById);
      push(t.error.title, err instanceof Error ? err.message : undefined);
      return;
    }

    const verb = t.review[status as keyof typeof t.review] ?? status;
    pushAction(`已${verb} ${segment_ids.length} 段`, "撤销", () => {
      void guard(async () => {
        await restorePriorStatuses(priorById);
        patchSegmentStatuses(priorById);
        await reloadSession();
        setQueueVersion((v) => v + 1);
      })();
    });

    // Reconcile session-level review_status in the background (AFTER the optimistic update,
    // so the UI never blocks on it).
    void reloadSession().catch(() => undefined);
    // Refetch the review queue so a session that just lost its last pending segment drops out.
    setQueueVersion((v) => v + 1);
  }

  // OPTIMISTIC accept-整场: mark every still-pending segment accepted at once, call
  // accept-remaining, offer Undo. Only the pending segments are affected (so Undo only
  // reverts those back to pending — already-rejected/needs_fix segments are left alone).
  async function handleAcceptSession() {
    if (!session) return;
    const pendingIds = session.segments.filter((s) => s.review_status === "pending_review").map((s) => s.segment_id);
    if (pendingIds.length === 0) return;
    const priorById = new Map<string, ReviewStatus>(pendingIds.map((id) => [id, "pending_review" as ReviewStatus]));

    patchSegmentStatuses(new Map(pendingIds.map((id) => [id, "accepted" as ReviewStatus])));

    try {
      await api.acceptRemaining(session.session_id);
    } catch (err) {
      patchSegmentStatuses(priorById);
      push(t.error.title, err instanceof Error ? err.message : undefined);
      return;
    }

    pushAction(`已${t.review.accepted} ${pendingIds.length} 段`, "撤销", () => {
      void guard(async () => {
        await api.clearReview(pendingIds);
        patchSegmentStatuses(priorById);
        await reloadSession();
        setQueueVersion((v) => v + 1);
      })();
    });

    void reloadSession().catch(() => undefined);
    // The whole session is now reviewed — refetch the queue so it leaves the inbox.
    setQueueVersion((v) => v + 1);
  }

  function highlightEvidence(candidateId: string) {
    const candidate = (llm?.memory_candidates ?? []).find((c) => c.candidate_id === candidateId);
    setHighlightedSegmentId(candidate?.evidence_segment_ids?.[0] ?? null);
    // The cited segment lives in the 审核 (review) tab; jump there so the highlight is visible
    // (panels are no longer co-mounted, so setting the id alone would leave the user on 观点).
    setTab("review");
  }

  const speakers = session ? Array.from(new Set(session.segments.map((s) => s.speaker))) : [];
  const gateOn = health?.require_accepted_transcripts ?? false;

  // Summary-derived counts (the SSE stream no longer carries the full task array).
  const counts = summary?.status_counts ?? {};
  const summaryTotal = summary?.total ?? 0;
  const activeCount = ACTIVE_STATUSES.reduce((n, s) => n + (counts[s] ?? 0), 0);
  // Prefer the backend's settled-task count (it knows retryable-but-exhausted failures count
  // as done); fall back to total-minus-active only for an older backend without done_total.
  const doneCount =
    summary?.done_total ??
    (summaryTotal > 0 ? summaryTotal - (counts["pending"] ?? 0) - (counts["claimed"] ?? 0) - (counts["running"] ?? 0) : 0);

  const firstRun = summaryTotal === 0 && days.length === 0;

  const importing = !!import_progress?.active;
  // The pipeline is "running" if importing, the worker is alive, OR a task is mid-flight.
  const pipelineRunning = importing || worker_running || activeCount > 0;

  // Re-list days whenever the pipeline finishes (running -> idle), so a completed run
  // surfaces its new day without a manual refresh.
  const wasPipelineRunning = useRef(false);
  useEffect(() => {
    if (wasPipelineRunning.current && !pipelineRunning) {
      void refreshDays().catch((err) => push(t.error.title, err instanceof Error ? err.message : undefined));
    }
    wasPipelineRunning.current = pipelineRunning;
  }, [pipelineRunning]);

  // Live progress: import phase first (copying files), then transcription/processing
  // driven by the compact summary counts. At idle / 100% the bar disappears.
  const inFlight = worker_running || activeCount > 0;
  const current: Stage = summary?.active_stage ? stageForTaskType(summary.active_stage) : "device";
  const total = importing ? import_progress!.total : inFlight ? summaryTotal : 0;
  const done = importing ? import_progress!.done : doneCount;
  const progressLabel = importing
    ? `导入 ${import_progress!.current || "…"}`
    : summary?.active_stage
      ? taskTypeZh(summary.active_stage)
      : STAGES.find((s) => s.id === current)?.label;

  // Per-stage breakdown + ETA need the full task list (durations live there, not in the
  // summary), so they show in the always-visible header without the heavy task fetch.
  const stageBreakdown = stageBreakdownFromSummary(summary?.stage_counts);
  const etaSeconds = summary?.eta_seconds ?? null;
  // The summary's failed_total counts terminal + retry-exhausted failures (matching the
  // backend's "done" semantics); the task-list fallback over-counts retryable failures that
  // still have attempts left, but only applies when the summary hasn't arrived yet.
  const failedCount = summary?.failed_total ?? tasks.filter((tk) => tk.status.startsWith("failed")).length;

  // ⌘K command set, rebuilt from current state each render: jump to any tab, open any
  // loaded day (-> 审核), or run a global action. Keep to navigation/tab jumps for now —
  // only actions trivially callable from App scope.
  const TAB_LABELS: Record<TabId, string> = { home: "首页", ingest: "录入", review: "审核", speakers: "声纹", llm: "观点", settings: "设置" };
  const commands: Command[] = [
    ...(Object.keys(TAB_LABELS) as TabId[]).map((id) => ({
      id: `tab-${id}`,
      title: `前往「${TAB_LABELS[id]}」`,
      group: "导航",
      keywords: `${id} ${TAB_LABELS[id]} tab`,
      run: () => setTab(id)
    })),
    ...days.map(({ day }) => ({
      id: `day-${day}`,
      title: `打开 ${day}`,
      group: "日期",
      keywords: `day ${day} 审核`,
      run: () => {
        void guard(selectDay)(day);
        setTab("review");
      }
    })),
    {
      id: "action-refresh-days",
      title: "刷新日期列表",
      group: "操作",
      keywords: "refresh days reload",
      run: () => void refreshDays().catch(() => undefined)
    }
  ];

  // Async transcript-search hits rendered as palette items: a snippet (matched substring bolded)
  // titled with `{day} · {speaker}`, that jumps to the utterance when chosen.
  const searchItems: Command[] = searchResults.map((r) => ({
    id: `search-${r.segment_id}`,
    title: r.text,
    node: highlightMatch(r.text, searchQuery.trim()),
    hint: `${r.day} · ${r.speaker}`,
    group: "转写搜索",
    run: () => void guard(jumpToSegment)(r.segment_id, r.session_id)
  }));

  // The bootstrap-error screen pre-empts every tab: 重试 (refreshBootstrap) is the sole
  // recovery path, so don't let a tab render an empty workspace behind it.
  if (bootstrapError && !bootstrapped) {
    return (
      <main className="workbench">
        <header className="workbench-header">
          <h1>{t.app.title}</h1>
        </header>
        <section className="tab-page single">
          <div className="empty error-state" role="alert">
            <Icon name="run" className="empty-icon" />
            <h3>后端或 API 不可用</h3>
            <p>{bootstrapError}</p>
            <button className="primary" onClick={() => void refreshBootstrap()}>
              <Icon name="refresh" /> 重试
            </button>
          </div>
        </section>
        <Toasts toasts={toasts} onDismiss={dismiss} />
      </main>
    );
  }

  // 首页 (home): the default landing — actionable cards that deep-link into each tab.
  function renderHome() {
    return (
      <HomePanel
        onGoReview={() => setTab("review")}
        onGoSpeakers={() => setTab("speakers")}
        onGoLlm={(day) => {
          void guard(selectDay)(day);
          setTab("llm");
        }}
        onOpenSession={(sid, day) => {
          void guard(async () => {
            await selectDay(day);
            await selectSession(sid);
          })();
          setTab("review");
        }}
      />
    );
  }

  // 录入 (ingest): device detection + import + the run-control/task surface.
  function renderIngest() {
    return (
      <div className="tab-page two-col">
        <div className="col-main">
          <div id="panel-device">
            <DevicePanel sources={sources ?? []} onImport={guard(handleImport)} onRefresh={() => void refreshDevices()} />
          </div>
        </div>
        <div className="col-side">
          <div id="panel-run">
            <RunInspector
              workerRunning={pipelineRunning}
              taskCount={summaryTotal}
              gateOn={gateOn}
              onRun={guard(() => api.run())}
              onStop={guard(() => api.stop())}
            />
          </div>
          <TaskList
            tasks={tasks}
            taskCount={summaryTotal}
            failedCount={failedCount}
            onToggle={(open) => {
              setTasksOpen(open);
              if (open) void refreshTasks().catch(() => undefined);
            }}
            onRetry={guard(async (taskId: string) => { await api.retry(taskId); await api.run(); await refreshTasks(); })}
            onRetryAllFailed={guard(async () => { await api.retryFailed(); await api.run(); await refreshTasks(); })}
          />
        </div>
      </div>
    );
  }

  // 审核 (review): the day/session picker on the left, the selected session's transcript +
  // speaker mapping on the right. Empty states when no day/session is chosen.
  function renderReview() {
    return (
      <div className="tab-page two-col">
        <aside className="col-nav">
          <div className="nav-mode" role="tablist" aria-label="审核浏览方式">
            <button
              type="button"
              role="tab"
              aria-selected={reviewMode === "queue"}
              className={`nav-mode-btn${reviewMode === "queue" ? " active" : ""}`}
              onClick={() => setReviewMode("queue")}
            >
              待审队列
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={reviewMode === "days"}
              className={`nav-mode-btn${reviewMode === "days" ? " active" : ""}`}
              onClick={() => setReviewMode("days")}
            >
              按天浏览
            </button>
          </div>
          {reviewMode === "queue" ? (
            <ReviewQueue
              activeSessionId={selectedSessionId}
              version={queueVersion}
              onOpen={(sid, day) => {
                void guard(async () => {
                  await selectDay(day);
                  await selectSession(sid);
                })();
              }}
            />
          ) : (
            <WorkspaceNav
              days={days}
              dayStatus={dayStatus}
              selectedDay={selectedDay}
              selectedSessionId={selectedSessionId}
              sessionsVersion={sessionsVersion}
              onSelectDay={(d) => void guard(selectDay)(d)}
              onSelectSession={(id) => void guard(selectSession)(id)}
              onRenameSession={(id, name) => guard(handleRenameSession)(id, name)}
              onDeleteSession={(id) => guard(handleDeleteSession)(id)}
            />
          )}
        </aside>
        <section className="col-content" id="panel-transcript">
          {session ? (
            <>
              <TranscriptReviewPanel
                session={session}
                persons={persons ?? []}
                highlightedSegmentId={highlightedSegmentId}
                onBatchReview={handleBatchReview}
                onAcceptSession={handleAcceptSession}
                onPlaybackError={(message) => push("音频播放失败", message)}
              />
              <SpeakerPanel
                speakers={speakers}
                persons={persons ?? []}
                onAssign={guard(async (speaker, personId) => { await api.assignPerson(speaker, personId); await reloadSession(); })}
                onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
              />
            </>
          ) : firstRun ? (
            <div className="empty">
              <Icon name="device" className="empty-icon" />
              <h3>{t.empty.firstRun}</h3>
              <p>{t.empty.firstRunHint}</p>
            </div>
          ) : !selectedDay ? (
            <div className="empty">
              <Icon name="inbox" className="empty-icon" />
              <h3>{t.empty.pickDay}</h3>
              <p>{t.empty.pickDayHint}</p>
            </div>
          ) : (
            <div className="empty">
              <Icon name="clock" className="empty-icon" />
              <h3>{t.empty.pickSession}</h3>
              <p>{t.empty.pickSessionHint}</p>
            </div>
          )}
        </section>
      </div>
    );
  }

  // 声纹 (speakers): the identity workflow is the hero — extract voiceprints, 框选+标注 on the
  // map, then 全局识别. Layout: a toolbar (inspect-day + 提取声纹/情绪), a 2-column main row
  // (map | people), then collapsible 对话分析 (Dynamics/Emotion) + 高级 (legacy cluster merge).
  function renderSpeakers() {
    // ONE inspected-day source of truth: the date input is fully controlled by
    // clusterDay (seeded by a day selected in 审核 via selectedDay), and that SAME
    // derived day drives every panel so they never disagree.
    const inspectDay = clusterDay || selectedDay;
    // 非发言人 (噪音/多人) labels — passed to the analytics charts so noise is filtered out and
    // can't masquerade as a real speaker.
    const nonSpeakerLabels = new Set(
      (people ?? []).filter((p) => p.person_type === "non_speaker").map((p) => p.display_name)
    );
    return (
      <div className="tab-page single speakers-layout">
        {/* Toolbar: a short title + the inspect-day picker + the 提取声纹/情绪 control. */}
        <section className="speakers-toolbar card">
          <div className="speakers-toolbar-title">
            <Icon name="mic" />
            <div>
              <strong>声纹身份</strong>
              <span className="muted"> — 在图上框选标注,再点全局识别</span>
            </div>
          </div>
          <label className="speakers-day">
            <span className="muted">{t.cluster.day}</span>
            <input
              type="date"
              aria-label={t.cluster.day}
              value={clusterDay || selectedDay || ""}
              onChange={(e) => setClusterDay(e.target.value)}
            />
          </label>
          <div className="speakers-extract">
            <VoiceprintPanel
              day={inspectDay}
              sessionId={selectedSessionId}
              persons={persons ?? []}
              onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
              onPlaybackError={(message) => push("音频播放失败", message)}
              push={push}
              onMatched={onPeopleChanged}
            />
          </div>
        </section>

        <VoiceprintWorkflowPanel
          selectedScopeCount={scope.days.length + scope.session_ids.length}
          projection={voiceprintProjectionState}
          selectedSegmentCount={voiceprintSelectedCount}
          hasKnownPeople={(people ?? []).some((p) => p.person_type !== "non_speaker" && p.enrolled)}
          lastAutoAttributeCount={lastAutoAttributeCount}
          hasReviewTarget={!!selectedSessionId || days.length > 0}
        />

        {/* Main row: the projection controls rail, the map (hero), the labeling/identify controls. */}
        <div className="speakers-main speakers-main-proj">
          <div className="speakers-proj-rail">
            <ScopeSelector
              value={scope}
              onChange={(next) => {
                setScope(next);
                // A scope change auto-applies (re-projects with the current params).
                setAppliedRequest(buildRequest(next, projParams));
                setVoiceprintProjectionState({ status: "idle" });
                setVoiceprintSelectedCount(0);
                setLastAutoAttributeCount(null);
              }}
            />
            <ProjectionControls
              value={projParams}
              onChange={(next) => {
                setProjParams(next);
                // A method switch auto-applies; pure param (slider/dropdown) edits wait for 投射.
                if (next.method !== projParams.method) setAppliedRequest(buildRequest(scope, next));
              }}
              onApply={() => setAppliedRequest(buildRequest(scope, projParams))}
              capped={projCapped?.capped}
              n={projCapped?.n}
              total={projCapped?.total}
            />
          </div>
          <div className="speakers-map">
            <VoiceprintMap
              key={mapKey}
              request={appliedRequest}
              onResult={(r) => setProjCapped(r)}
              onState={setVoiceprintProjectionState}
              onSelectionChange={setVoiceprintSelectedCount}
              onPlaybackError={(message) => push("音频播放失败", message)}
              people={people ?? []}
              onLabel={async (personId, segmentIds) => {
                await api.labelSegments(personId, segmentIds);
                push(`已标注 ${segmentIds.length} 段`);
              }}
              onChanged={onPeopleChanged}
            />
          </div>
          <div className="speakers-people">
            <PeoplePanel
              sessionId={selectedSessionId}
              day={inspectDay}
              onChanged={onPeopleChanged}
              push={push}
              pushAction={pushAction}
            />
          </div>
        </div>

        {/* 对话分析 — session analytics, secondary to identity; collapsed by default. */}
        <details
          className="speakers-analysis card"
          onToggle={(e) => setAnalysisOpen((e.currentTarget as HTMLDetailsElement).open)}
        >
          <summary>对话分析(发言占比 · 情绪)</summary>
          <div className="speakers-analysis-body">
            {analysisOpen ? (
              <>
                <DynamicsCharts sessionId={selectedSessionId} nonSpeakerLabels={nonSpeakerLabels} />
                <EmotionCharts sessionId={selectedSessionId} nonSpeakerLabels={nonSpeakerLabels} />
              </>
            ) : null}
          </div>
        </details>

        {/* 高级 — the legacy day-cluster merge tool; tucked away, collapsed by default. */}
        <details className="speakers-advanced card">
          <summary>高级 — 按天聚类合并(旧版)</summary>
          <div className="speakers-advanced-body">
            {inspectDay ? (
              <ClusterPanel
                key={inspectDay}
                day={inspectDay}
                persons={persons ?? []}
                onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
                onPlaybackError={(message) => push("音频播放失败", message)}
              />
            ) : (
              <p className="muted">选择一个日期后可使用按天聚类合并。</p>
            )}
          </div>
        </details>
      </div>
    );
  }

  // 观点 (llm): the per-session editable workspace is the hero (edit transcript/prompt/result,
  // manual generate + publish to Obsidian). A 日报汇总 toggle exposes the legacy read-only daily
  // rollup (LlmResultPanel) behind a day picker.
  function renderLlm() {
    const modeToggle = (
      <div className="llm-mode card" role="tablist" aria-label="观点视图">
        <button
          type="button"
          role="tab"
          aria-selected={llmMode === "session"}
          className={`nav-mode-btn${llmMode === "session" ? " active" : ""}`}
          onClick={() => setLlmMode("session")}
        >
          会话观点
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={llmMode === "daily"}
          className={`nav-mode-btn${llmMode === "daily" ? " active" : ""}`}
          onClick={() => setLlmMode("daily")}
        >
          日报汇总
        </button>
      </div>
    );

    if (llmMode === "session") {
      return (
        <div className="tab-page single is-reading">
          {modeToggle}
          <ViewpointWorkspace
            initialDay={selectedDay}
            onPlaybackError={(message) => push("音频播放失败", message)}
          />
        </div>
      );
    }

    return (
      <div className="tab-page single is-reading">
        {modeToggle}
        <div className="llm-daypick card">
          <label htmlFor="llm-day">观点日期</label>
          <select
            id="llm-day"
            value={selectedDay ?? ""}
            disabled={days.length === 0}
            onChange={(e) => {
              if (e.target.value) void guard(selectDay)(e.target.value);
            }}
          >
            <option value="" disabled>
              {days.length === 0 ? "暂无有数据的日期" : "选择有数据的日期…"}
            </option>
            {days.map((d) => (
              <option key={d.day} value={d.day}>
                {dayLabel(d.day)} · {d.session_count} 场
              </option>
            ))}
          </select>
        </div>
        {llm ? (
          <LlmResultPanel result={llm} onHighlightEvidence={highlightEvidence} />
        ) : (
          <div className="empty">
            <Icon name="inbox" className="empty-icon" />
            <h3>{t.empty.pickDay}</h3>
            <p>{t.empty.pickDayHint}</p>
          </div>
        )}
      </div>
    );
  }

  // 设置 (settings).
  function renderSettings() {
    return (
      <div className="tab-page single is-reading">
        <SettingsPanel />
      </div>
    );
  }

  function renderTab() {
    switch (tab) {
      case "home":
        return renderHome();
      case "ingest":
        return renderIngest();
      case "review":
        return renderReview();
      case "speakers":
        return renderSpeakers();
      case "llm":
        return renderLlm();
      case "settings":
        return renderSettings();
    }
  }

  return (
    <main className="workbench tabbed">
      <header className="workbench-header">
        <div className="header-status">
          <h1>{t.app.title}</h1>
          <span className={pipelineRunning ? "live" : "dim"}>
            {pipelineRunning ? <span className="live-dot" aria-hidden /> : null}
            {pipelineRunning ? t.app.running : t.app.idle}
          </span>
          <Progress
            done={done}
            total={total}
            label={progressLabel}
            stages={importing ? undefined : stageBreakdown}
            etaSeconds={importing ? null : etaSeconds}
          />
        </div>
        <div className="header-actions">
          <ThemeToggle />
          <Tabs active={tab} onSelect={setTab} />
        </div>
      </header>

      {renderTab()}

      <CommandPalette
        open={paletteOpen}
        commands={commands}
        extraItems={searchItems}
        onQueryChange={setSearchQuery}
        onClose={() => {
          setPaletteOpen(false);
          setSearchQuery("");
          setSearchResults([]);
        }}
      />
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
