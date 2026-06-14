import type { Stage } from "../lib/stages";
import { STAGES } from "../lib/stages";
import { Icon } from "./Icon";

export function PipelineRail({
  activeStage,
  focusedStage,
  onSelect
}: {
  activeStage: Stage;
  focusedStage?: Stage;
  onSelect?: (stage: Stage) => void;
}) {
  const activeIndex = STAGES.findIndex((s) => s.id === activeStage);
  return (
    <nav className="spine" aria-label="流水线阶段">
      {STAGES.map((stage, i) => {
        const cls = [
          "stage",
          stage.id === activeStage ? "active" : "",
          activeIndex >= 0 && i < activeIndex ? "done" : "",
          stage.id === focusedStage ? "focused" : ""
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <span key={stage.id} style={{ display: "inline-flex", alignItems: "center" }}>
            <button
              type="button"
              className={cls}
              aria-label={`阶段：${stage.label}`}
              onClick={() => onSelect?.(stage.id)}
            >
              {stage.id === activeStage ? <span className="live-dot" aria-hidden /> : null}
              {stage.label}
            </button>
            {i < STAGES.length - 1 ? <Icon name="chevron" className="icon sep" /> : null}
          </span>
        );
      })}
    </nav>
  );
}
