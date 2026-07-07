import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { ViewpointContent, ViewpointState } from "../../api/types";
import { Icon } from "../../components/Icon";

const STATUS_ZH: Record<ViewpointState["status"], string> = {
  draft: "草稿",
  edited: "已编辑",
  published: "已发布"
};

/** Deep clone so local edits never mutate the parent's state object in place. */
function clone(c: ViewpointContent): ViewpointContent {
  return JSON.parse(JSON.stringify(c)) as ViewpointContent;
}

/**
 * The right pane's result editor: 重新生成 + the editable 观点 document + 确认保存到 Obsidian.
 * The whole Content doc is held locally and edited field-by-field; `evidence_refs` and
 * `speaker_cluster_id` are preserved verbatim (read-only) because the backend validates them.
 * Each commit assembles the FULL doc and PUTs it; a 400's message shows inline without losing
 * the in-progress edit. Generation is delegated to the parent (which owns polling).
 */
export function ResultEditor({
  vp,
  onChanged,
  onGenerate
}: {
  vp: ViewpointState;
  onChanged: () => void;
  onGenerate: () => void;
}) {
  // A local, editable copy of the effective doc. Re-seeded whenever the server's effective doc
  // changes identity (a regenerate / revert), but NOT on every keystroke (so typing isn't lost).
  const [doc, setDoc] = useState<ViewpointContent | null>(vp.effective ? clone(vp.effective) : null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState<string | null>(vp.note_path);
  const identityBlocked = vp.identity_review ? !vp.identity_review.can_summarize : false;

  useEffect(() => {
    setDoc(vp.effective ? clone(vp.effective) : null);
    setError(null);
  }, [vp.effective]);

  useEffect(() => setPublished(vp.note_path), [vp.note_path]);

  const commit = async (next: ViewpointContent) => {
    setBusy(true);
    setError(null);
    try {
      await api.editViewpoint(vp.session_id, next);
      onChanged();
    } catch (err) {
      // Keep the user's edit on screen; just surface what the backend rejected.
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const regenerate = () => {
    if (vp.edited && !window.confirm("重新生成会丢弃你对结果的修改,确定?")) return;
    onGenerate();
  };

  const revert = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.clearViewpointEdit(vp.session_id);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "撤销失败");
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.publishViewpoint(vp.session_id);
      setPublished(r.note_path);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发布失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="vp-result card">
      <div className="vp-result-head">
        <div className="section-title" style={{ margin: 0 }}>
          <Icon name="viewpoint" /> 总结结果
          <span className={`status s-${vp.status}`}>{STATUS_ZH[vp.status]}</span>
          {vp.published_at ? <span className="dim num">{vp.published_at}</span> : null}
        </div>
        <button type="button" className="primary" disabled={busy || vp.generating || identityBlocked} onClick={regenerate}>
          {vp.generating ? <span className="spinner" aria-hidden /> : <Icon name="refresh" />} 重新生成
        </button>
      </div>

      <div className="vp-result-body">
        {identityBlocked ? <p className="vp-error">请先在「身份」确认本场参与人；允许未知说话人存在，但不允许名单外人名进入总结。</p> : null}
        {vp.generating ? (
          <div className="vp-generating" role="status">
            <span className="spinner" aria-hidden /> 生成中…
          </div>
        ) : !vp.has_generated || !doc ? (
          <div className="empty">
            <Icon name="inbox" className="empty-icon" />
            <h3>尚未生成</h3>
            <p>点击「重新生成」生成本会话的总结。</p>
          </div>
        ) : (
          <ResultFields doc={doc} setDoc={setDoc} busy={busy} onCommit={commit} />
        )}

        {error ? <p className="vp-error" role="alert">{error}</p> : null}
      </div>

      <div className="vp-result-actions">
        {vp.edited ? (
          <button type="button" className="ghost" disabled={busy} onClick={() => void revert()}>
            撤销修改(恢复生成版)
          </button>
        ) : null}
        <button type="button" className="primary" disabled={busy || !vp.has_generated} onClick={() => void publish()}>
          <Icon name="link" /> 确认保存到 Obsidian
        </button>
      </div>
      {published ? <p className="vp-published">已发布:{published}</p> : null}
    </section>
  );
}

/** The editable Content fields. Local edits update `doc`; committing (blur / list mutation)
 *  assembles the full doc and calls `onCommit`. */
function ResultFields({
  doc,
  setDoc,
  busy,
  onCommit
}: {
  doc: ViewpointContent;
  setDoc: (d: ViewpointContent) => void;
  busy: boolean;
  onCommit: (d: ViewpointContent) => void;
}) {
  // Patch the local doc immutably, returning the next doc (so callers can commit it).
  const patch = (fn: (d: ViewpointContent) => void): ViewpointContent => {
    const next = clone(doc);
    fn(next);
    setDoc(next);
    return next;
  };

  return (
    <div className="vp-fields">
      <label className="vp-field">
        <span className="vp-field-label">标题</span>
        <input
          aria-label="标题"
          value={doc.headline}
          disabled={busy}
          onChange={(e) => patch((d) => { d.headline = e.target.value; })}
          onBlur={() => onCommit(doc)}
        />
      </label>

      <label className="vp-field">
        <span className="vp-field-label">摘要</span>
        <textarea
          aria-label="摘要"
          value={doc.summary}
          rows={3}
          disabled={busy}
          onChange={(e) => patch((d) => { d.summary = e.target.value; })}
          onBlur={() => onCommit(doc)}
        />
      </label>

      <StringList
        title="话题"
        items={doc.topics}
        busy={busy}
        onItem={(i, v) => onCommit(patch((d) => { d.topics[i] = v; }))}
        onAdd={() => onCommit(patch((d) => { d.topics.push(""); }))}
        onRemove={(i) => onCommit(patch((d) => { d.topics.splice(i, 1); }))}
      />

      <StringList
        title="核心结论"
        items={doc.core_conclusions}
        busy={busy}
        onItem={(i, v) => onCommit(patch((d) => { d.core_conclusions[i] = v; }))}
        onAdd={() => onCommit(patch((d) => { d.core_conclusions.push(""); }))}
        onRemove={(i) => onCommit(patch((d) => { d.core_conclusions.splice(i, 1); }))}
      />

      <StringList
        title="待解决问题"
        items={doc.open_questions}
        busy={busy}
        onItem={(i, v) => onCommit(patch((d) => { d.open_questions[i] = v; }))}
        onAdd={() => onCommit(patch((d) => { d.open_questions.push(""); }))}
        onRemove={(i) => onCommit(patch((d) => { d.open_questions.splice(i, 1); }))}
      />

      <fieldset className="vp-group">
        <legend>决策</legend>
        {doc.decisions.map((dec, i) => (
          <div className="vp-row" key={i}>
            <textarea
              aria-label={`决策 ${i + 1}`}
              value={dec.text}
              rows={2}
              disabled={busy}
              onChange={(e) => patch((d) => { d.decisions[i].text = e.target.value; })}
              onBlur={() => onCommit(doc)}
            />
            {dec.evidence_refs.length ? <span className="vp-refs dim">证据 {dec.evidence_refs.join(", ")}</span> : null}
          </div>
        ))}
      </fieldset>

      <fieldset className="vp-group">
        <legend>待办</legend>
        {doc.todos.map((td, i) => (
          <div className="vp-row" key={i}>
            <textarea
              aria-label={`待办 ${i + 1}`}
              value={td.text}
              rows={2}
              disabled={busy}
              onChange={(e) => patch((d) => { d.todos[i].text = e.target.value; })}
              onBlur={() => onCommit(doc)}
            />
            <input
              aria-label={`待办负责人 ${i + 1}`}
              className="vp-owner"
              placeholder="负责人"
              value={td.owner}
              disabled={busy}
              onChange={(e) => patch((d) => { d.todos[i].owner = e.target.value; })}
              onBlur={() => onCommit(doc)}
            />
            {td.evidence_refs.length ? <span className="vp-refs dim">证据 {td.evidence_refs.join(", ")}</span> : null}
          </div>
        ))}
      </fieldset>

      {doc.per_speaker.map((spk, si) => (
        <fieldset className="vp-group vp-speaker" key={spk.speaker_cluster_id || si}>
          <legend>发言人 · <span className="dim num">{spk.speaker_cluster_id}</span></legend>
          {spk.viewpoints.map((vpt, vi) => (
            <div className="vp-row" key={vi}>
              <textarea
                aria-label={`${spk.speaker_cluster_id} 观点 ${vi + 1}`}
                value={vpt.text}
                rows={2}
                disabled={busy}
                onChange={(e) => patch((d) => { d.per_speaker[si].viewpoints[vi].text = e.target.value; })}
                onBlur={() => onCommit(doc)}
              />
              {vpt.evidence_refs.length ? <span className="vp-refs dim">证据 {vpt.evidence_refs.join(", ")}</span> : null}
            </div>
          ))}
          <div className="vp-speaker-meta">
            <label className="vp-field">
              <span className="vp-field-label">情绪</span>
              <input
                aria-label={`${spk.speaker_cluster_id} 情绪`}
                value={spk.sentiment}
                disabled={busy}
                onChange={(e) => patch((d) => { d.per_speaker[si].sentiment = e.target.value; })}
                onBlur={() => onCommit(doc)}
              />
            </label>
            <label className="vp-field">
              <span className="vp-field-label">立场</span>
              <input
                aria-label={`${spk.speaker_cluster_id} 立场`}
                value={spk.stance}
                disabled={busy}
                onChange={(e) => patch((d) => { d.per_speaker[si].stance = e.target.value; })}
                onBlur={() => onCommit(doc)}
              />
            </label>
          </div>
          <StringList
            title="潜在需求"
            items={spk.latent_needs}
            busy={busy}
            onItem={(i, v) => onCommit(patch((d) => { d.per_speaker[si].latent_needs[i] = v; }))}
            onAdd={() => onCommit(patch((d) => { d.per_speaker[si].latent_needs.push(""); }))}
            onRemove={(i) => onCommit(patch((d) => { d.per_speaker[si].latent_needs.splice(i, 1); }))}
          />
        </fieldset>
      ))}
    </div>
  );
}

/** An editable string list with add/remove; commits on blur and on add/remove. */
function StringList({
  title,
  items,
  busy,
  onItem,
  onAdd,
  onRemove
}: {
  title: string;
  items: string[];
  busy: boolean;
  onItem: (index: number, value: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
}) {
  // Local mirror so typing doesn't commit on every keystroke (we commit on blur).
  const [local, setLocal] = useState(items);
  useEffect(() => setLocal(items), [items]);

  return (
    <fieldset className="vp-group vp-list">
      <legend>{title}</legend>
      {local.map((v, i) => (
        <div className="vp-list-row" key={i}>
          <input
            aria-label={`${title} ${i + 1}`}
            value={v}
            disabled={busy}
            onChange={(e) => setLocal((arr) => arr.map((x, j) => (j === i ? e.target.value : x)))}
            onBlur={() => onItem(i, local[i])}
          />
          <button type="button" className="ghost ghost-sm" aria-label={`删除 ${title} ${i + 1}`} disabled={busy} onClick={() => onRemove(i)}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="ghost ghost-sm" disabled={busy} onClick={onAdd}>+ 添加{title}</button>
    </fieldset>
  );
}
