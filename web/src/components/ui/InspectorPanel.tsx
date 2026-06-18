import type { ReactNode } from "react";

export function InspectorPanel({
  title,
  subtitle,
  actions,
  children,
  className
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <aside className={`inspector-panel card${className ? ` ${className}` : ""}`} aria-label={title}>
      <header className="inspector-head">
        <div>
          <div className="section-title" style={{ margin: 0 }}>
            {title}
          </div>
          {subtitle ? <p className="inspector-subtitle muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="inspector-actions">{actions}</div> : null}
      </header>
      <div className="inspector-body">{children}</div>
    </aside>
  );
}
