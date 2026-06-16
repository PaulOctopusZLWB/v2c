import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
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
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { stageForTaskType, STAGES } from "./lib/stages";
import type { Stage } from "./lib/stages";
import { taskTypeZh } from "./lib/format";
import { t } from "./i18n";
import type { DailyLlmResult, DayStatusRow, Health, ImportSource, Person, TaskRow, TranscriptSession } from "./api/types";

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
  const { toasts, push, dismiss } = useToasts();
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
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<string | null>(null);
  const [focusedStage, setFocusedStage] = useState<Stage | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);
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
      const [personsResult, healthResult, daysResult, devicesResult] = await Promise.all([
        api.persons(),
        api.health(),
        api.days(),
        api.devices()
      ]);
      setPersons(personsResult.persons ?? []);
      setHealth(healthResult);
      setDays(daysResult.days ?? []);
      setSources(devicesResult.sources ?? []);
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

  function highlightEvidence(candidateId: string) {
    const candidate = (llm?.memory_candidates ?? []).find((c) => c.candidate_id === candidateId);
    setHighlightedSegmentId(candidate?.evidence_segment_ids?.[0] ?? null);
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

  // Map a pipeline stage to the DOM id of the panel that owns it, then scroll there.
  const STAGE_PANEL_ID: Record<Stage, string> = {
    device: "panel-device",
    import: "panel-device",
    asr: "panel-transcript",
    review: "panel-transcript",
    llm: "panel-llm",
    publish: "panel-run"
  };
  function focusStage(stage: Stage) {
    setFocusedStage(stage);
    const el = document.getElementById(STAGE_PANEL_ID[stage]);
    el?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }

  function renderCenter() {
    if (bootstrapError && !bootstrapped) {
      return (
        <div className="empty error-state" role="alert">
          <Icon name="run" className="empty-icon" />
          <h3>后端或 API 不可用</h3>
          <p>{bootstrapError}</p>
          <button className="primary" onClick={() => void refreshBootstrap()}>
            <Icon name="refresh" /> 重试
          </button>
        </div>
      );
    }
    if (session) {
      return (
        <>
          <TranscriptReviewPanel
            session={session}
            persons={persons ?? []}
            highlightedSegmentId={highlightedSegmentId}
            onBatchReview={guard(async (ids, status) => { await api.batchReview(ids, status); await reloadSession(); })}
            onAcceptSession={guard(async () => { await api.acceptRemaining(session.session_id); await reloadSession(); })}
            onPlaybackError={(message) => push("音频播放失败", message)}
          />
          <SpeakerPanel
            speakers={speakers}
            persons={persons ?? []}
            onAssign={guard(async (speaker, personId) => { await api.assignPerson(speaker, personId); await reloadSession(); })}
            onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
          />
        </>
      );
    }
    if (firstRun) {
      return (
        <div className="empty">
          <Icon name="device" className="empty-icon" />
          <h3>{t.empty.firstRun}</h3>
          <p>{t.empty.firstRunHint}</p>
        </div>
      );
    }
    if (!selectedDay) {
      return (
        <div className="empty">
          <Icon name="inbox" className="empty-icon" />
          <h3>{t.empty.pickDay}</h3>
          <p>{t.empty.pickDayHint}</p>
        </div>
      );
    }
    return (
      <div className="empty">
        <Icon name="clock" className="empty-icon" />
        <h3>{t.empty.pickSession}</h3>
        <p>{t.empty.pickSessionHint}</p>
      </div>
    );
  }

  return (
    <main className="workbench">
      <header className="workbench-header">
        <h1>{t.app.title}</h1>
        <span className={pipelineRunning ? "live" : "dim"}>
          {pipelineRunning ? <span className="live-dot" aria-hidden /> : null}
          {pipelineRunning ? t.app.running : t.app.idle}
        </span>
        <PipelineRail activeStage={current} focusedStage={focusedStage ?? undefined} onSelect={focusStage} />
        <Progress
          done={done}
          total={total}
          label={progressLabel}
          stages={importing ? undefined : stageBreakdown}
          etaSeconds={importing ? null : etaSeconds}
        />
      </header>

      <aside className="rail-left">
        <div id="panel-device">
          <DevicePanel sources={sources ?? []} onImport={guard(handleImport)} onRefresh={() => void refreshDevices()} />
        </div>
        <WorkspaceNav
          days={days}
          dayStatus={dayStatus}
          selectedDay={selectedDay}
          selectedSessionId={selectedSessionId}
          onSelectDay={(d) => void guard(selectDay)(d)}
          onSelectSession={(id) => void guard(selectSession)(id)}
        />
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
      </aside>

      <section className="center-panel" id="panel-transcript">
        {renderCenter()}
      </section>

      <aside className="rail-right">
        <div id="panel-run">
          <RunInspector
            workerRunning={pipelineRunning}
            taskCount={summaryTotal}
            gateOn={gateOn}
            onRun={guard(() => api.run())}
            onStop={guard(() => api.stop())}
          />
        </div>
        <div id="panel-llm">
          {llm ? <LlmResultPanel result={llm} onHighlightEvidence={highlightEvidence} /> : null}
        </div>
        <div id="panel-config">
          {bootstrapped ? (
          <>
          <SettingsPanel />
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
          {(selectedDay ?? clusterDay) ? (
            <ClusterPanel
              key={selectedDay ?? clusterDay}
              day={selectedDay ?? clusterDay}
              persons={persons ?? []}
              onCreatePerson={guard(async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); })}
              onPlaybackError={(message) => push("音频播放失败", message)}
            />
          ) : null}
          </>
          ) : null}
        </div>
      </aside>

      <Toasts toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
