import { useEffect, useState } from "react";
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
  const { tasks, worker_running } = usePipelineStatus();
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

  useEffect(() => {
    api.persons().then((r) => setPersons(r.persons ?? [])).catch(() => undefined);
    api.health().then((h) => setHealth(h)).catch(() => undefined);
    api.days().then((r) => setDays(r.days ?? [])).catch(() => undefined);
    void refreshDevices();
    const timer = setInterval(() => void refreshDevices(), DEVICE_POLL_MS);
    return () => clearInterval(timer);
  }, []);

  async function handleImport(root: string) {
    if (!root) return;
    await api.importDir(root);
    await api.run(); // import only enqueues; explicitly start the background worker
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

  // The pipeline is "running" if the worker is alive OR a task is mid-flight.
  const pipelineRunning = worker_running || tasks.some((tk) => tk.status === "running");

  // Live progress derived from the SSE-fed task list. Only meaningful while
  // there is genuine in-flight work — at idle / 100% the bar disappears.
  const inFlight = worker_running || tasks.some((tk) => !TERMINAL.includes(tk.status));
  const total = inFlight ? tasks.length : 0;
  const done = tasks.filter((tk) => TERMINAL.includes(tk.status)).length;
  const current = activeStage(tasks);
  const progressLabel = STAGES.find((s) => s.id === current)?.label;

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
          selectedDay={selectedDay}
          selectedSessionId={selectedSessionId}
          onSelectDay={(d) => void guard(selectDay)(d)}
          onSelectSession={(id) => void guard(selectSession)(id)}
        />
        <TaskList tasks={tasks} onRetry={guard((taskId: string) => api.retry(taskId))} />
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
