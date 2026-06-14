import type { Stage } from "../lib/stages";
import { STAGES } from "../lib/stages";

export function PipelineRail({ activeStage }: { activeStage: Stage }) {
  return (
    <nav className="spine" aria-label="流水线阶段">
      {STAGES.map((stage, i) => (
        <span className={stage.id === activeStage ? "stage active" : "stage"} key={stage.id}>
          {stage.id === activeStage ? <span className="live-dot" aria-hidden /> : null}
          {stage.label}
          {i < STAGES.length - 1 ? <span className="sep" aria-hidden> ▸ </span> : null}
        </span>
      ))}
    </nav>
  );
}
