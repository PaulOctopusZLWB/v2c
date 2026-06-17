import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { Progress } from "./components/Progress";
import { RunInspector } from "./components/RunInspector";
import { SettingsPanel } from "./components/SettingsPanel";
import { ClusterPanel } from "./components/ClusterPanel";
import { TaskList } from "./components/TaskList";
import { Icon } from "./components/Icon";
import { Toasts, useToasts } from "./components/Toasts";
import { DevicePanel } from "./features/device/DevicePanel";
import { WorkspaceNav } from "./features/workspace/WorkspaceNav";
import { TranscriptReviewPanel } from "./features/transcript/TranscriptReviewPanel";
import { SpeakerPanel } from "./features/speakers/SpeakerPanel";
import { VoiceprintPanel } from "./features/speakers/VoiceprintPanel";
import { PeoplePanel } from "./features/people/PeoplePanel";
import { VoiceprintMap } from "./features/viz/VoiceprintMap";
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { Tabs } from "./features/workspace/Tabs";
import { useTab } from "./features/workspace/useTab";
import type { TabId } from "./features/workspace/useTab";
import { CommandPalette, type Command } from "./features/command/CommandPalette";
import { useHotkeys } from "./features/command/useHotkeys";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { stageForTaskType, STAGES } from "./lib/stages";
import type { Stage } from "./lib/stages";
import { taskTypeZh } from "./lib/format";
import { t } from "./i18n";
import type { DailyLlmResult, DayStatusRow, Health, ImportSource, Person, PersonRow, ReviewStatus, TaskRow, TranscriptSession } from "./api/types";

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
  const [persons, setPersons] = useState<Person[]>([]);
  // "People taught once": the enriched person roster (enrollment + attribution) for the 声纹 tab,
  // plus a key that, when bumped, remounts the voiceprint map so it refetches recoloured points.
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [mapKey, setMapKey] = useState(0);
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<string | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);
  // ⌘K command palette (keyboard-driven launcher); closed by default.
  const [paletteOpen, setPaletteOpen] = useState(false);
  useHotkeys({ "mod+k": (e) => { e.preventDefault(); setPaletteOpen((v) => !v); } });
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

  async function reloadSession() {
    if (selectedSessionId) setSession(await api.session(selectedSessionId));
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
      })();
    });

    // Reconcile session-level review_status in the background (AFTER the optimistic update,
    // so the UI never blocks on it).
    void reloadSession().catch(() => undefined);
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
      })();
    });

    void reloadSession().catch(() => undefined);
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
  const TAB_LABELS: Record<TabId, string> = { ingest: "录入", review: "审核", speakers: "声纹", llm: "观点", settings: "设置" };
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
          <WorkspaceNav
            days={days}
            dayStatus={dayStatus}
            selectedDay={selectedDay}
            selectedSessionId={selectedSessionId}
            onSelectDay={(d) => void guard(selectDay)(d)}
            onSelectSession={(id) => void guard(selectSession)(id)}
          />
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

  // 声纹 (speakers): voiceprint coverage/anchor/recluster for the selected session, plus the
  // day-level diarization-cluster merge tool. Scoped to the day/session chosen in 审核.
  function renderSpeakers() {
    const day = selectedDay ?? clusterDay;
    return (
      <div className="tab-page single">
        <section className="cluster-day card">
          <div className="section-title">{t.cluster.title}</div>
          <label className="settings-field">
            <span>{t.cluster.day}</span>
            <input
              type="date"
              aria-label={t.cluster.day}
              value={selectedDay ?? clusterDay}
              onChange={(e) => setClusterDay(e.target.value)}
            />
          </label>
        </section>
        <VoiceprintMap
          key={mapKey}
          sessionId={selectedSessionId}
          day={clusterDay || selectedDay}
          onPlaybackError={(message) => push("音频播放失败", message)}
          people={people ?? []}
          onLabel={async (personId, segmentIds) => {
            await api.labelSegments(personId, segmentIds);
            push(`已标注 ${segmentIds.length} 段`);
          }}
          onChanged={onPeopleChanged}
        />
        <PeoplePanel
          sessionId={selectedSessionId}
          day={clusterDay || selectedDay}
          onChanged={onPeopleChanged}
          push={push}
          pushAction={pushAction}
        />
        <VoiceprintPanel
          day={selectedDay}
          sessionId={selectedSessionId}
          persons={persons ?? []}
          onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
          onPlaybackError={(message) => push("音频播放失败", message)}
        />
        {day ? (
          <ClusterPanel
            key={day}
            day={day}
            persons={persons ?? []}
            onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
            onPlaybackError={(message) => push("音频播放失败", message)}
          />
        ) : null}
      </div>
    );
  }

  // 观点 (llm): the day's generated context + memory candidates.
  function renderLlm() {
    return (
      <div className="tab-page single">
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
      <div className="tab-page single">
        <SettingsPanel />
      </div>
    );
  }

  function renderTab() {
    switch (tab) {
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
        <Tabs active={tab} onSelect={setTab} />
      </header>

      {renderTab()}

      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
