import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { ViewpointPrompt } from "../../api/types";
import { Icon } from "../../components/Icon";

/**
 * The right pane's collapsible prompt editor. Seeds a textarea from the *effective* prompt and
 * lets the user save a per-session override (the primary action), reset it (clear the override,
 * falling back to the global default), or — secondarily — promote the text to the global default.
 * Every save calls `onChanged` so the parent re-loads the prompt block (so `is_override` updates).
 */
export function PromptEditor({
  sessionId,
  prompt,
  onChanged
}: {
  sessionId: string;
  prompt: ViewpointPrompt;
  onChanged: () => void;
}) {
  const [text, setText] = useState(prompt.effective);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seed when the effective prompt changes (e.g. after a reset reverts to the default).
  useEffect(() => setText(prompt.effective), [prompt.effective]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <details className="vp-prompt card">
      <summary>
        <Icon name="viewpoint" /> 会话提示词
        {prompt.is_override ? <span className="vp-badge">本会话自定义</span> : null}
      </summary>
      <div className="vp-prompt-body">
        <textarea
          aria-label="会话提示词"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          rows={6}
        />
        <div className="vp-prompt-actions">
          <button type="button" className="primary" disabled={busy} onClick={() => void run(() => api.setSessionPromptOverride(sessionId, text))}>
            {busy ? <span className="spinner" aria-hidden /> : null}保存(本会话)
          </button>
          <button type="button" className="ghost" disabled={busy} onClick={() => void run(() => api.setSessionPromptOverride(sessionId, null))}>
            重置
          </button>
          <button type="button" className="ghost ghost-sm" disabled={busy} onClick={() => void run(() => api.setSessionPrompt(text))}>
            设为全局默认
          </button>
        </div>
        {error ? <p className="vp-error" role="alert">{error}</p> : null}
      </div>
    </details>
  );
}
