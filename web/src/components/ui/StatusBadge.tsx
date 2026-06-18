import type { ReactNode } from "react";

export type SemanticStatus =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "accepted"
  | "rejected"
  | "needs_fix"
  | "pending_review"
  | "blocked"
  | "draft"
  | "edited"
  | "published";

const STATUS_CLASS: Record<SemanticStatus, string> = {
  neutral: "s-pending_review",
  info: "s-info",
  success: "s-accepted",
  warning: "s-needs_fix",
  danger: "s-rejected",
  accepted: "s-accepted",
  rejected: "s-rejected",
  needs_fix: "s-needs_fix",
  pending_review: "s-pending_review",
  blocked: "s-blocked",
  draft: "s-draft",
  edited: "s-edited",
  published: "s-published"
};

export function StatusBadge({
  status,
  children,
  className
}: {
  status: SemanticStatus;
  children: ReactNode;
  className?: string;
}) {
  return <span className={`badge ${STATUS_CLASS[status]}${className ? ` ${className}` : ""}`}>{children}</span>;
}
