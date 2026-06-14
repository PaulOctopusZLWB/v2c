import type { Stage } from "../lib/stages";
import { STAGES } from "../lib/stages";

export function PipelineRail({
  activeStage,
  focusedStage,
  onSelect
}: {
  activeStage: Stage;
  focusedStage?: Stage;
  onSelect?: (stage: Stage) => void;
}) {
  return (
    <nav className="spine" aria-label="流水线阶段">
      {STAGES.map((stage, i) => {
        const cls = [
          "stage",
          stage.id === activeStage ? "active" : "",
          stage.id === focusedStage ? "focused" : ""
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <button
            type="button"
            className={cls}
            key={stage.id}
            aria-label={`阶段：${stage.label}`}
            onClick={() => onSelect?.(stage.id)}
          >
            {stage.id === activeStage ? <span className="live-dot" aria-hidden /> : null}
            {stage.label}
            {i < STAGES.length - 1 ? <span className="sep" aria-hidden> ▸ </span> : null}
          </button>
        );
      })}
    </nav>
  );
}
