import type { DailyLlmResult } from "../../api/types";

export function LlmResultPanel({ result }: { result: DailyLlmResult }) {
  const summary = result.context?.content?.["summary"];
  return (
    <section>
      <h2>LLM Result — {result.day}</h2>
      {summary ? <p>{String(summary)}</p> : <p>No daily context generated yet.</p>}
      <h3>Memory candidates (read-only)</h3>
      <p>Confirm or reject these in Obsidian — the panel shows them for review only.</p>
      {result.memory_candidates.map((candidate) => (
        <div className="candidate" key={candidate.candidate_id}>
          <strong>{candidate.edited_claim ?? candidate.candidate_claim}</strong>
          <span> · {candidate.claim_type} · {Math.round(candidate.confidence * 100)}% · {candidate.status}</span>
        </div>
      ))}
    </section>
  );
}
