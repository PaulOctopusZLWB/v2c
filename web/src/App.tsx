import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
import { Progress } from "./components/Progress";
import { RunInspector } from "./components/RunInspector";
import { TaskList } from "./components/TaskList";
import { Icon } from "./components/Icon";
import { Toasts, useToasts } from "./components/Toasts";
import { DevicePanel } from "./features/device/DevicePanel";
import { WorkspaceNav } from "./features/workspace/WorkspaceNav";
import { TranscriptReviewPanel } from "./features/transcript/TranscriptReviewPanel";
import { SpeakerPanel } from "./features/speakers/SpeakerPanel";
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { activeStage, STAGES } from "./lib/stages";
import type { Stage } from "./lib/stages";
import { t } from "./i18n";
import type { DailyLlmResult, Health, ImportSource, Person, TranscriptSession } from "./api/types";

const DEVICE_POLL_MS = 5000;
const TERMINAL = ["succeeded", "failed_terminal", "failed_retryable", "failed"];

export function App() {
  const { tasks, worker_running, import_progress } = usePipelineStatus();
  const { toasts, push, dismiss } = useToasts();
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<TranscriptSession | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<string | null>(null);
  const [focusedStage, setFocusedStage] = useState<Stage | null>(null);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);

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
    const result = await api.days();
    setDays(result.days ?? []);
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
    const timer = setInterval(() => void refreshDevices(), DEVICE_POLL_MS);
    return () => clearInterval(timer);
  }, []);

  async function handleImport(root: string) {
    if (!root) return;
    await api.importDir(root);
    await api.run(); // import only enqueues; explicitly start the background worker
    await refreshDays(); // a fresh import yields a new day; reflect it immediately
  }

  async function selectDay(day: string) {
    setSelectedDay(day);
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

  // All segment ids cited by any viewpoint candidate get an evidence badge.
  const evidenceSegmentIds = new Set(
    (llm?.memory_candidates ?? []).flatMap((c) => c.evidence_segment_ids ?? [])
  );

  const speakers = session ? Array.from(new Set(session.segments.map((s) => s.speaker))) : [];
  const gateOn = health?.require_accepted_transcripts ?? false;
  const firstRun = tasks.length === 0 && days.length === 0;

  const importing = !!import_progress?.active;
  // The pipeline is "running" if importing, the worker is alive, OR a task is mid-flight.
  const pipelineRunning = importing || worker_running || tasks.some((tk) => tk.status === "running");

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
  // from the SSE-fed task list. At idle / 100% the bar disappears.
  const inFlight = worker_running || tasks.some((tk) => !TERMINAL.includes(tk.status));
  const current = activeStage(tasks);
  const total = importing ? import_progress!.total : inFlight ? tasks.length : 0;
  const done = importing ? import_progress!.done : tasks.filter((tk) => TERMINAL.includes(tk.status)).length;
  const progressLabel = importing
    ? `导入 ${import_progress!.current || "…"}`
    : STAGES.find((s) => s.id === current)?.label;

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
        <div className="empty error-state">
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
            evidenceSegmentIds={evidenceSegmentIds}
            onReview={guard(async (id, status) => { await api.reviewSegment(id, status); await reloadSession(); })}
            onOverride={guard(async (id, personId) => { await api.overridePerson(id, personId); await reloadSession(); })}
            onPlay={() => undefined}
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
        <Progress done={done} total={total} label={progressLabel} />
      </header>

      <aside className="rail-left">
        <div id="panel-device">
          <DevicePanel sources={sources ?? []} onImport={guard(handleImport)} onRefresh={() => void refreshDevices()} />
        </div>
        <WorkspaceNav
          days={days}
          selectedDay={selectedDay}
          selectedSessionId={selectedSessionId}
          onSelectDay={(d) => void guard(selectDay)(d)}
          onSelectSession={(id) => void guard(selectSession)(id)}
        />
        <TaskList tasks={tasks} onRetry={guard(async (taskId: string) => { await api.retry(taskId); await api.run(); })} />
      </aside>

      <section className="center-panel" id="panel-transcript">
        {renderCenter()}
      </section>

      <aside className="rail-right">
        <div id="panel-run">
          <RunInspector
            workerRunning={pipelineRunning}
            taskCount={tasks.length}
            gateOn={gateOn}
            onRun={guard(() => api.run())}
            onStop={guard(() => api.stop())}
          />
        </div>
        <div id="panel-llm">
          {llm ? <LlmResultPanel result={llm} onHighlightEvidence={highlightEvidence} /> : null}
        </div>
      </aside>

      <Toasts toasts={toasts} onDismiss={dismiss} />
    </main>
  );
}
