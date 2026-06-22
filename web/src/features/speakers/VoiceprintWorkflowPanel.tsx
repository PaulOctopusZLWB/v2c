import { StatusBadge, WorkflowStepper } from "../../components/ui";
import { buildVoiceprintWorkflow, type VoiceprintWorkflowInput } from "./voiceprintWorkflow";

export function VoiceprintWorkflowPanel(props: VoiceprintWorkflowInput) {
  const steps = buildVoiceprintWorkflow(props);
  const s = props.status;
  const ready = !!s && s.total > 0 && s.unidentified === 0;
  return (
    <section className="voiceprint-workflow card">
      <div className="workflow-head">
        <div>
          <div className="section-title" style={{ margin: 0 }}>
            声纹主路径
          </div>
          <p className="workflow-subtitle muted">提取声纹 -&gt; 自动聚类 -&gt; 分配聚类 -&gt; 清理噪音 -&gt; 确认(未识别=0)</p>
        </div>
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
      </div>
      <WorkflowStepper ariaLabel="声纹主路径" steps={steps} />
    </section>
  );
}
