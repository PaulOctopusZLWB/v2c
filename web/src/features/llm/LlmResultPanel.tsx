import type { DailyLlmResult } from "../../api/types";
import { t } from "../../i18n";

export function LlmResultPanel({
  result, onHighlightEvidence
}: {
  result: DailyLlmResult;
  onHighlightEvidence?: (candidateId: string) => void;
}) {
  const summary = result.context?.content?.["summary"];
  return (
    <section className="llm-panel">
      <h3>{t.viewpoint.title} · {result.day} <span className="dim">({t.viewpoint.readOnly})</span></h3>
      {summary ? <p>{String(summary)}</p> : <p className="dim">{t.viewpoint.none}</p>}
      <p className="dim">{t.viewpoint.confirmInObsidian} ↗</p>
      {result.memory_candidates.map((c) => (
        <div className="viewpoint" key={c.candidate_id} onClick={() => onHighlightEvidence?.(c.candidate_id)}>
          <strong>◆ {c.edited_claim ?? c.candidate_claim}</strong>
          <span className="dim num"> · {c.claim_type} · {Math.round(c.confidence * 100)}% · {c.status}</span>
        </div>
      ))}
    </section>
  );
}
