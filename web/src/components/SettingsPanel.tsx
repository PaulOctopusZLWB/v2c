import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AsrMode, Settings } from "../api/types";
import { t } from "../i18n";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { Icon } from "./Icon";

const GLM_PRESETS = ["glm-5.1", "glm-4-flash"] as const;

/**
 * 模型设置 — edit the web-settable model/runtime overrides (ASR mode, preset speaker
 * count, GLM model/endpoint/thinking). Loads the effective settings, and on save PUTs
 * only the fields the user changed. Changes take effect on the next run, not immediately.
 * `GLM_API_KEY` is intentionally absent — it stays env-managed.
 */
export function SettingsPanel({ onSaved }: { onSaved?: () => void } = {}) {
  // The last-loaded server state, used to diff which fields actually changed.
  const [saved, setSaved] = useState<Settings | null>(null);
  // The working draft the form edits.
  const [draft, setDraft] = useState<Settings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function load() {
    setLoadError(null);
    try {
      const s = await api.settings();
      setSaved(s);
      setDraft(s);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "加载失败");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const save = useAsyncAction(async () => {
    if (!draft || !saved) return;
    // Only send the fields that differ from the last-loaded server state.
    const patch: Partial<Settings> = {};
    if (draft.asr_mode !== saved.asr_mode) patch.asr_mode = draft.asr_mode;
    if (draft.asr_preset_spk_num !== saved.asr_preset_spk_num) patch.asr_preset_spk_num = draft.asr_preset_spk_num;
    if (draft.glm_model !== saved.glm_model) patch.glm_model = draft.glm_model;
    if (draft.glm_base_url !== saved.glm_base_url) patch.glm_base_url = draft.glm_base_url;
    if (draft.glm_thinking !== saved.glm_thinking) patch.glm_thinking = draft.glm_thinking;
    const next = await api.updateSettings(patch);
    setSaved(next);
    setDraft(next);
    onSaved?.();
  });

  if (loadError) {
    return (
      <section className="settings-panel card">
        <div className="section-title">{t.settings.title}</div>
        <p className="muted" role="alert">{loadError}</p>
      </section>
    );
  }
  if (!draft) {
    return (
      <section className="settings-panel card">
        <div className="section-title">{t.settings.title}</div>
        <p className="muted">…</p>
      </section>
    );
  }

  return (
    <section className="settings-panel card">
      <div className="section-title">{t.settings.title}</div>

      <label className="settings-field">
        <span>{t.settings.asrMode}</span>
        <select
          aria-label={t.settings.asrMode}
          value={draft.asr_mode}
          disabled={save.pending}
          onChange={(e) => setDraft({ ...draft, asr_mode: e.target.value as AsrMode })}
        >
          <option value="chunk">{t.settings.asrChunk}</option>
          <option value="diarize">{t.settings.asrDiarize}</option>
        </select>
      </label>

      <label className="settings-field">
        <span>{t.settings.presetSpkNum}</span>
        <input
          type="number"
          min={1}
          aria-label={t.settings.presetSpkNum}
          placeholder={t.settings.presetSpkNumAuto}
          value={draft.asr_preset_spk_num ?? ""}
          disabled={save.pending}
          onChange={(e) => {
            const v = e.target.value.trim();
            setDraft({ ...draft, asr_preset_spk_num: v === "" ? null : Number(v) });
          }}
        />
      </label>

      <label className="settings-field">
        <span>{t.settings.glmModel}</span>
        <input
          type="text"
          list="glm-model-presets"
          aria-label={t.settings.glmModel}
          value={draft.glm_model}
          disabled={save.pending}
          onChange={(e) => setDraft({ ...draft, glm_model: e.target.value })}
        />
        <datalist id="glm-model-presets">
          {GLM_PRESETS.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </label>

      <label className="settings-field">
        <span>{t.settings.glmBaseUrl}</span>
        <input
          type="text"
          aria-label={t.settings.glmBaseUrl}
          value={draft.glm_base_url}
          disabled={save.pending}
          onChange={(e) => setDraft({ ...draft, glm_base_url: e.target.value })}
        />
      </label>

      <label className="settings-field settings-check">
        <input
          type="checkbox"
          aria-label={t.settings.glmThinking}
          checked={draft.glm_thinking}
          disabled={save.pending}
          onChange={(e) => setDraft({ ...draft, glm_thinking: e.target.checked })}
        />
        <span>{t.settings.glmThinking}</span>
      </label>

      <div className="settings-actions">
        <button className="primary" onClick={() => void save.run()} disabled={save.pending} aria-busy={save.pending}>
          {save.pending ? <span className="spinner" aria-hidden /> : <Icon name="check_circle" />}
          {save.pending ? t.settings.saving : t.settings.save}
        </button>
        <span className="muted settings-hint">{t.settings.nextRunHint}</span>
      </div>
    </section>
  );
}
