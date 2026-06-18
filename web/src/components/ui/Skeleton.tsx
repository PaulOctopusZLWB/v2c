export function Skeleton({
  label,
  rows = 3,
  className
}: {
  label: string;
  rows?: number;
  className?: string;
}) {
  return (
    <div className={`skeleton${className ? ` ${className}` : ""}`} role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, index) => (
        <span key={index} className="skeleton-row" />
      ))}
    </div>
  );
}
