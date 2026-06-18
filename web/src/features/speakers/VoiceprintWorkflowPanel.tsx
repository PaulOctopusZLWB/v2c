import { WorkflowStepper } from "../../components/ui";
import { buildVoiceprintWorkflow, type VoiceprintWorkflowInput } from "./voiceprintWorkflow";

export function VoiceprintWorkflowPanel(props: VoiceprintWorkflowInput) {
  const steps = buildVoiceprintWorkflow(props);
  return (
    <section className="voiceprint-workflow card">
      <div className="workflow-head">
        <div>
          <div className="section-title" style={{ margin: 0 }}>
            声纹主路径
          </div>
          <p className="workflow-subtitle muted">选择范围 -&gt; 投射 -&gt; 框选/标注 -&gt; 全局识别 -&gt; 回审核验证</p>
        </div>
      </div>
      <WorkflowStepper ariaLabel="声纹主路径" steps={steps} />
    </section>
  );
}
