import { useEffect, useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
import { RunInspector } from "./components/RunInspector";
import { TaskList } from "./components/TaskList";
import { WorkspaceNav } from "./features/workspace/WorkspaceNav";
import { TranscriptReviewPanel } from "./features/transcript/TranscriptReviewPanel";
import { SpeakerPanel } from "./features/speakers/SpeakerPanel";
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { activeStage } from "./lib/stages";
import type { DailyLlmResult, Person, TranscriptSession } from "./api/types";

export function App() {
  const { tasks, worker_running } = usePipelineStatus();
  const [sourceDir, setSourceDir] = useState("");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [session, setSession] = useState<TranscriptSession | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);

  useEffect(() => {
    api.persons().then((r) => setPersons(r.persons ?? [])).catch(() => undefined);
  }, []);

  async function handleImport() {
    if (!sourceDir) return;
    await api.importDir(sourceDir);
    await api.run(); // default import enqueues only; explicitly start the background worker
  }

  async function selectDay(day: string) {
    setSelectedDay(day);
    setLlm(await api.dailyLlm(day));
  }

  async function reloadSession() {
    if (session) setSession(await api.session(session.session_id));
  }

  const speakers = session ? Array.from(new Set(session.segments.map((s) => s.speaker))) : [];

  return (
    <main className="workbench">
      <aside className="pipeline-rail">
        <PipelineRail activeStage={activeStage(tasks)} />
        <WorkspaceNav selectedDay={selectedDay} onSelectDay={selectDay} onSelectSession={async (id) => setSession(await api.session(id))} />
      </aside>
      <section className="main-panel">
        <h1>Personal Context Node</h1>
        <label>
          Source directory
          <input value={sourceDir} onChange={(event) => setSourceDir(event.target.value)} placeholder="/path/to/recordings" />
        </label>
        <button onClick={handleImport}>Import</button>
        <TaskList tasks={tasks} onRetry={(taskId) => api.retry(taskId)} />
        {session ? (
          <TranscriptReviewPanel
            session={session}
            persons={persons}
            onReview={async (id, status) => { await api.reviewSegment(id, status); await reloadSession(); }}
            onOverride={async (id, personId) => { await api.overridePerson(id, personId); await reloadSession(); }}
            onPlay={(id) => { void new Audio(api.audioUrl(id)).play(); }}
          />
        ) : null}
        {session ? (
          <SpeakerPanel
            speakers={speakers}
            persons={persons}
            onAssign={async (speaker, personId) => { await api.assignPerson(speaker, personId); await reloadSession(); }}
            onCreatePerson={async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); }}
          />
        ) : null}
        {llm ? <LlmResultPanel result={llm} /> : null}
      </section>
      <RunInspector workerRunning={worker_running} taskCount={tasks.length} onRun={() => api.run()} onStop={() => api.stop()} />
    </main>
  );
}
