import type { DailyLlmResult } from "../../api/types";
import { t } from "../../i18n";
import { Icon } from "../../components/Icon";

function statusZh(status: string): string {
  const m: Record<string, string> = {
    pending: "待定",
    proposed: "已提出",
    accepted: "接受",
    rejected: "拒绝",
    confirmed: "已确认"
  };
  return m[status] ?? status;
}

export function LlmResultPanel({
  result, onHighlightEvidence
}: {
  result: DailyLlmResult;
  onHighlightEvidence?: (candidateId: string) => void;
}) {
  const summary = result.context?.content?.["summary"];
  return (
    <section className="llm-panel card">
      <div className="section-title">
        <Icon name="viewpoint" /> {t.viewpoint.title} · <span className="num">{result.day}</span>
        <span className="dim">（{t.viewpoint.readOnly}）</span>
      </div>
      {summary ? <p className="muted">{String(summary)}</p> : <p className="dim">{t.viewpoint.none}</p>}
      <p className="dim">
        {t.viewpoint.confirmInObsidian} <Icon name="link" />
      </p>
      {result.memory_candidates.map((c) => (
        <button className="viewpoint" type="button" key={c.candidate_id} onClick={() => onHighlightEvidence?.(c.candidate_id)}>
          <span className="claim">
            <Icon name="viewpoint" /> {c.edited_claim ?? c.candidate_claim}
          </span>
          <span className="meta num">
            {c.claim_type} · {Math.round(c.confidence * 100)}% · {statusZh(c.status)}
          </span>
        </button>
      ))}
    </section>
  );
}
