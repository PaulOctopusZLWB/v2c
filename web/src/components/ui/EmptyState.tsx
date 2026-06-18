import { Icon } from "../Icon";
import { Button } from "./Button";

export function EmptyState({
  icon,
  title,
  description,
  actionLabel,
  onAction,
  tone = "default",
  className
}: {
  icon: string;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  tone?: "default" | "error";
  className?: string;
}) {
  return (
    <div
      className={`empty${tone === "error" ? " error-state" : ""}${className ? ` ${className}` : ""}`}
      role={tone === "error" ? "alert" : undefined}
    >
      <Icon name={icon} className="empty-icon" />
      <h3>{title}</h3>
      {description ? <p>{description}</p> : null}
      {actionLabel && onAction ? (
        <Button variant="primary" icon="refresh" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
