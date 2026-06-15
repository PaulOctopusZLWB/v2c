import { useCallback, useRef, useState } from "react";

export interface Toast {
  id: number;
  title: string;
  message?: string;
}

const AUTO_DISMISS_MS = 5000;

/** Tiny transient-error toast store: push errors, auto-dismiss after ~5s. */
export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (title: string, message?: string) => {
      const id = ++seq.current;
      setToasts((list) => [...list, { id, title, message }]);
      setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
      return id;
    },
    [dismiss]
  );

  return { toasts, push, dismiss };
}

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  if (toasts.length === 0) return null;
  return (
    // Each toast is its own role="alert" live region; the container is a plain wrapper so
    // we don't nest an assertive alert inside a polite status region (ARIA anti-pattern).
    <div className="toasts">
      {toasts.map((toast) => (
        <div className="toast" key={toast.id} role="alert">
          <div className="t-title">{toast.title}</div>
          {toast.message ? <div className="dim">{toast.message}</div> : null}
          <button className="icon-btn ghost" type="button" aria-label="关闭通知" onClick={() => onDismiss(toast.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
