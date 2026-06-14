import { useEffect, useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
import { RunInspector } from "./components/RunInspector";
import { TaskList } from "./components/TaskList";
import { DevicePanel } from "./features/device/DevicePanel";
import { WorkspaceNav } from "./features/workspace/WorkspaceNav";
import { TranscriptReviewPanel } from "./features/transcript/TranscriptReviewPanel";
import { SpeakerPanel } from "./features/speakers/SpeakerPanel";
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { activeStage } from "./lib/stages";
import { t } from "./i18n";
import type { DailyLlmResult, Health, ImportSource, Person, TranscriptSession } from "./api/types";

const DEVICE_POLL_MS = 5000;

export function App() {
  const { tasks, worker_running } = usePipelineStatus();
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<TranscriptSession | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);
  const [highlightedSegmentId, setHighlightedSegmentId] = useState<string | null>(null);

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
    const candidate = (llm?.memory_candidates ?? []).find((c) => c.candidate_id === candidateId) as
      | (DailyLlmResult["memory_candidates"][number] & { evidence_segment_id?: string | null })
      | undefined;
    setHighlightedSegmentId(candidate?.evidence_segment_id ?? null);
  }

  const speakers = session ? Array.from(new Set(session.segments.map((s) => s.speaker))) : [];
  const gateOn = health?.require_accepted_transcripts ?? false;
  const firstRun = tasks.length === 0 && days.length === 0;

  return (
    <main className="workbench">
      <header className="workbench-header">
        <h1>{t.app.title}</h1>
        <span className={worker_running ? "live" : "dim"}>
          {worker_running ? <span className="live-dot" aria-hidden /> : null}
          {worker_running ? t.app.running : t.app.idle}
        </span>
        <PipelineRail activeStage={activeStage(tasks)} />
      </header>

      <aside className="rail-left">
        <DevicePanel sources={sources ?? []} onImport={handleImport} onRefresh={() => void refreshDevices()} />
        <WorkspaceNav selectedDay={selectedDay} onSelectDay={selectDay} onSelectSession={selectSession} />
        <TaskList tasks={tasks} onRetry={(taskId) => api.retry(taskId)} />
      </aside>

      <section className="center-panel">
        {firstRun ? <p className="empty dim">{t.empty.firstRun}</p> : null}
        {session ? (
          <>
            <TranscriptReviewPanel
              session={session}
              persons={persons ?? []}
              highlightedSegmentId={highlightedSegmentId}
              onReview={async (id, status) => { await api.reviewSegment(id, status); await reloadSession(); }}
              onOverride={async (id, personId) => { await api.overridePerson(id, personId); await reloadSession(); }}
              onPlay={(id) => { void new Audio(api.audioUrl(id)).play(); }}
            />
            <SpeakerPanel
              speakers={speakers}
              persons={persons ?? []}
              onAssign={async (speaker, personId) => { await api.assignPerson(speaker, personId); await reloadSession(); }}
              onCreatePerson={async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); }}
            />
          </>
        ) : null}
      </section>

      <aside className="rail-right">
        <RunInspector
          workerRunning={worker_running}
          taskCount={tasks.length}
          gateOn={gateOn}
          onRun={() => api.run()}
          onStop={() => api.stop()}
        />
        {llm ? <LlmResultPanel result={llm} onHighlightEvidence={highlightEvidence} /> : null}
      </aside>
    </main>
  );
}
