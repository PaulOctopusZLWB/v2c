import type { ReactNode } from "react";
import { Icon } from "../Icon";

export type WorkflowStepState = "pending" | "current" | "running" | "complete" | "blocked" | "error";

export interface WorkflowStep {
  id: string;
  label: string;
  state: WorkflowStepState;
  detail?: string;
  action?: ReactNode;
}

const ICON_BY_STATE: Record<WorkflowStepState, string> = {
  pending: "clock",
  current: "viewpoint",
  running: "refresh",
  complete: "check_circle",
  blocked: "flag",
  error: "reject"
};

export function WorkflowStepper({
  steps,
  ariaLabel,
  className
}: {
  steps: WorkflowStep[];
  ariaLabel: string;
  className?: string;
}) {
  return (
    <ol className={`workflow-stepper${className ? ` ${className}` : ""}`} aria-label={ariaLabel}>
      {steps.map((step) => (
        <li key={step.id} className="workflow-step" data-state={step.state}>
          <span className="workflow-step-icon" aria-hidden>
            {step.state === "running" ? <span className="spinner" aria-hidden /> : <Icon name={ICON_BY_STATE[step.state]} />}
          </span>
          <span className="workflow-step-main">
            <span className="workflow-step-label">{step.label}</span>
            {step.detail ? <span className="workflow-step-detail">{step.detail}</span> : null}
          </span>
          {step.action ? <span className="workflow-step-action">{step.action}</span> : null}
        </li>
      ))}
    </ol>
  );
}
