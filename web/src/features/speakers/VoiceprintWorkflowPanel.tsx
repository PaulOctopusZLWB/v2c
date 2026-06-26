import { StatusBadge, WorkflowStepper } from "../../components/ui";
import { buildVoiceprintWorkflow, type VoiceprintWorkflowInput } from "./voiceprintWorkflow";

export function VoiceprintWorkflowPanel(props: VoiceprintWorkflowInput) {
  const steps = buildVoiceprintWorkflow(props);
  const s = props.status;
  const ready = !!s && s.total > 0 && s.unidentified === 0;
  return (
    <section className="voiceprint-workflow voiceprint-workflow-rail card" aria-label="声纹主路径">
      <WorkflowStepper ariaLabel="声纹步骤" steps={steps} className="workflow-stepper-compact" />
      {s ? (
        <div className="workflow-gate">
          {ready ? (
            <StatusBadge status="success">可进入汇总</StatusBadge>
          ) : (
            <StatusBadge status="warning">
              未识别 <span className="num">{s.unidentified}</span> 段
            </StatusBadge>
          )}
        </div>
      ) : null}
    </section>
  );
}
