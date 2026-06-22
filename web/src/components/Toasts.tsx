import { useCallback, useRef, useState } from "react";
import { Portal } from "./ui/Portal";

export interface Toast {
  id: number;
  title: string;
  message?: string;
  /** Optional action affordance (e.g. 撤销). Both must be present to render the button. */
  actionLabel?: string;
  onAction?: () => void;
}

const AUTO_DISMISS_MS = 5000;
const ACTION_DISMISS_MS = 6000;

/** Tiny transient toast store: push errors (auto-dismiss ~5s) or actionable toasts
 *  (a labeled button + longer auto-dismiss, e.g. an Undo affordance). */
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

  // An actionable toast: the message + a button (actionLabel) that runs onAction then
  // dismisses. It also auto-dismisses after `ms` (the action is then lost, not run).
  const pushAction = useCallback(
    (message: string, actionLabel: string, onAction: () => void, ms = ACTION_DISMISS_MS) => {
      const id = ++seq.current;
      const wrapped = () => {
        onAction();
        dismiss(id);
      };
      setToasts((list) => [...list, { id, title: message, actionLabel, onAction: wrapped }]);
      setTimeout(() => dismiss(id), ms);
      return id;
    },
    [dismiss]
  );

  return { toasts, push, pushAction, dismiss };
}

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  if (toasts.length === 0) return null;
  return (
    // Each toast is its own role="alert" live region; the container is a plain wrapper so
    // we don't nest an assertive alert inside a polite status region (ARIA anti-pattern).
    // Portalled to #overlay-root so toasts are never clipped by the active tab's overflow.
    <Portal>
    <div className="toasts">
      {toasts.map((toast) => (
        <div className="toast" key={toast.id} role="alert">
          <div className="t-title">{toast.title}</div>
          {toast.message ? <div className="dim">{toast.message}</div> : null}
          {toast.actionLabel && toast.onAction ? (
            <button className="toast-action" type="button" onClick={toast.onAction}>
              {toast.actionLabel}
            </button>
          ) : null}
          <button className="icon-btn ghost" type="button" aria-label="关闭通知" onClick={() => onDismiss(toast.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
    </Portal>
  );
}
