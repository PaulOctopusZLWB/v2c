import type { Stage } from "../lib/stages";
import { STAGES } from "../lib/stages";

export function PipelineRail({ activeStage }: { activeStage: Stage }) {
  return (
    <nav aria-label="Pipeline stages">
      {STAGES.map((stage) => (
        <div className={stage.id === activeStage ? "stage active" : "stage"} key={stage.id}>
          {stage.label}
        </div>
      ))}
    </nav>
  );
}
