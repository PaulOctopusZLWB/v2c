import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { matchesPerson, personOptions } from "../../lib/personSearch";
import { api } from "../../api/client";
import type { PersonRow, Suggestion } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";
import { Button, InspectorPanel, StatusBadge } from "../../components/ui";
import type { ConfirmFn } from "../../components/ui/Dialog";

const nameKey = (name: string) => name.normalize("NFKC").trim().toLowerCase();

/**
 * 人物 — the supervised-identity surface. Automatic matching runs in the background.
 *  - 智能建议 (suggest): for the selected session, score each diarization cluster against the
 *    enrolled centroids; one tap (采用) labels that whole cluster's segments as the person.
 *  - 登记声纹 (enroll): mostly automatic now (labeling auto-enrolls). Re-freezes the centroid from
 *    a person's manual labels — disabled until they have at least one manual label.
 * Every mutation calls onChanged() so the map (and its colours) refetch.
 */
export function PeoplePanel({
  sessionId,
  day,
  refreshVersion = 0,
  onChanged,
  push,
  confirm
}: {
  sessionId?: string | null;
  day?: string | null;
  refreshVersion?: number;
  onChanged: () => void;
  push: (title: string, message?: string, variant?: "success" | "error") => void;
  pushAction?: (message: string, actionLabel: string, onAction: () => void) => void;
  onAutoAttributed?: (count: number) => void;
  // App 的危险确认对话框(替代 window.confirm)。
  confirm: ConfirmFn;
}) {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadSequence = useRef(0);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [editingPersonId, setEditingPersonId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  // Noise tools retain their own scope after removing the global re-identification control.
  const [scope, setScope] = useState<"all" | "session">("all");
  // Auto-noise duration threshold (ms): segments shorter than this are bulk-marked noise.
  const [noiseMs, setNoiseMs] = useState(300);

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoadError(null);
    setLoading(true);
    try {
      const response = await api.people();
      if (sequence === loadSequence.current) setPeople(response.people ?? []);
    } catch (err) {
      if (sequence === loadSequence.current) setLoadError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => { loadSequence.current++; };
  }, [load, refreshVersion]);

  // A mutation succeeded: reload the local roster AND let the parent refetch the map.
  const refresh = useCallback(async () => {
    await load();
    onChanged();
  }, [load, onChanged]);

  const create = useAsyncAction(async (name: string) => {
    if (!name.trim()) return;
    try {
      setCreateError(null);
      await api.createPerson(name.trim());
      setNewName("");
    // refresh() (not just load()) so the parent's persons roster — and the assign-person
    // <select> fed by it — picks up the new person immediately, not just this panel's list.
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCreateError(message);
      push("新建人物失败", message, "error");
    }
  });

  const rename = useAsyncAction(async (person: PersonRow) => {
    const next = editName.trim();
    if (!next) { setEditError("人物名称不能为空"); return; }
    if (next === person.display_name) { setEditingPersonId(null); setEditError(null); return; }
    try {
      setEditError(null);
      await api.renamePerson(person.person_id, next, person.display_name);
      setEditingPersonId(null);
      await refresh();
      push("人物已改名", `${person.display_name} → ${next}`, "success");
    } catch (err) {
      setEditError(err instanceof Error ? err.message : String(err));
    }
  });

  // One-tap 噪音/多人 bucket: create a ready-to-use non_speaker person so the user can box-select
  // messy points on the map and mark them noise. Only offered when no non_speaker exists yet.
  const createNonSpeaker = useAsyncAction(async () => {
    try {
      await api.createPerson("噪音/多人", "non_speaker");
      push("已创建「噪音/多人」类别", undefined, "success");
      await refresh();
    } catch (err) {
      push("创建失败", err instanceof Error ? err.message : undefined);
    }
  });

  // Bulk-mark noise (filler text and/or short segments) into the noise bucket. Respects the 全部/
  // 本会话 scope toggle; never overwrites a manual label to a real person.
  const markNoise = useAsyncAction(async (kind: "filler" | "short") => {
    const useSession = scope === "session" && !!sessionId;
    try {
      const res = await api.markNoise({
        filler: kind === "filler",
        max_duration_ms: kind === "short" ? noiseMs : null,
        session_id: useSession ? sessionId : null,
        day: useSession ? null : day ?? null,
      });
      push(
        kind === "filler" ? "已标注语气词为噪音" : `已标注短段(<${noiseMs}ms)为噪音`,
        `本次新增 ${res.marked} 段 → ${res.noise_label}`,
      );
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : undefined;
      push("标注噪音失败", msg?.includes("400") ? "请先创建「噪音/多人」类别" : msg);
    }
  });

  // Delete an accidental duplicate person: confirm, cascade-delete on the backend, then reload
  // the roster + recolor the map. Never offered for 本人 (is_self) — the button is hidden for them.
  const remove = useAsyncAction(async (p: PersonRow) => {
    const ok = await confirm({
      title: `删除人物「${p.display_name}」?`,
      body: <>其声纹与全部归属将被<strong>清除</strong>,此操作不可撤销。</>,
      confirmLabel: "删除"
    });
    if (!ok) return;
    try {
      await api.deletePerson(p.person_id);
      push(`已删除人物「${p.display_name}」`, undefined, "success");
      await refresh();
    } catch (err) {
      push("删除失败", err instanceof Error ? err.message : undefined);
    }
  });

  const enroll = useAsyncAction(async (personId: string) => {
    try {
      const res = await api.enrollPerson(personId);
      push(`已登记 ${res.n_segments} 段声纹`, undefined, "success");
      await refresh();
    } catch (err) {
      // Defensive: enroll 400s when the person has 0 manual labels (the button is normally
      // disabled in that case, but surface a clear hint if it slips through).
      const msg = err instanceof Error ? err.message : undefined;
      push("登记失败", msg?.includes("400") ? "该人物还没有手动标注的片段,先标注几段再登记" : msg);
    }
  });

  const suggest = useAsyncAction(async () => {
    if (!sessionId) return;
    try {
      setSuggestions((await api.suggestPeople(sessionId)).suggestions ?? []);
    } catch (err) {
      push("获取建议失败", err instanceof Error ? err.message : undefined);
    }
  });

  // Adopt a suggestion: the suggestion only carries the cluster's *speaker*, so first fetch that
  // cluster's segments (scoped to the session) and then label their ids for the person.
  const adopt = useAsyncAction(async (s: Suggestion) => {
    if (!sessionId) return;
    try {
      const segs = (await api.speakerSegments({ session_id: sessionId, speaker: s.speaker })).segments ?? [];
      const ids = segs.map((seg) => seg.segment_id);
      if (ids.length === 0) {
        push("该聚类暂无可标注片段");
        return;
      }
      const res = await api.labelSegments(s.person_id, ids);
      push(`已将 ${s.speaker} 标注为 ${s.person_label}`, `${res.labeled} 段`, "success");
      // Drop the adopted suggestion so the row can't be double-applied.
      setSuggestions((cur) => (cur ?? []).filter((x) => x.speaker !== s.speaker));
      await refresh();
    } catch (err) {
      push("采用失败", err instanceof Error ? err.message : undefined);
    }
  });

  // Split the roster: real voiceprint identities vs. the 非发言人 (噪音/多人/无效) buckets, which
  // are labelable but NOT a voiceprint (no 登记声纹, rendered in a muted group at the bottom).
  const speakers = people.filter((p) => p.person_type !== "non_speaker");
  const nonSpeakers = people.filter((p) => p.person_type === "non_speaker");
  const hasNonSpeaker = nonSpeakers.length > 0;
  const sortedOptions = useMemo(() => personOptions(people), [people]);
  const byId = new Map(speakers.map((p) => [p.person_id, p]));
  const visiblePeople = sortedOptions.filter((p) => matchesPerson(p, query))
    .flatMap((p) => byId.has(p.value) ? [byId.get(p.value)!] : []);
  const hasDuplicate = (name: string, exclude?: string) => {
    const key = nameKey(name);
    return !!key && people.some((p) => p.person_id !== exclude && nameKey(p.display_name) === key);
  };
  const createDuplicate = hasDuplicate(newName);

  // The delete affordance for a person (shared by both groups) — never offered for 本人 (is_self).
  const deleteButton = (p: PersonRow) =>
    p.is_self ? null : (
      <button
        className="ghost ghost-sm person-delete"
        onClick={() => void remove.run(p)}
        disabled={remove.pending}
        aria-busy={remove.pending}
        title="删除该人物(其声纹与归属将被清除)"
      >
        {remove.pending ? <span className="spinner" aria-hidden /> : <Icon name="trash" />}
        删除
      </button>
    );

  return (
    <InspectorPanel
      title="人物证据"
      subtitle="查看人物名册、搜索人物与管理声纹"
      actions={
        <StatusBadge status="info">
          {loading ? "加载中…" : <><span className="num">{speakers.length}</span> 人</>}
        </StatusBadge>
      }
      className="people-panel"
    >
      <p className="people-explainer muted">
        在下方查看和搜索人物；新建、噪音分类等操作在「管理工具」中。
      </p>

      {loadError ? <div role="alert"><p>人物加载失败：{loadError}</p>
        <button type="button" className="ghost" onClick={() => void load()}>重新加载人物</button>
      </div> : null}

      <details className="people-roster" open>
        <summary><span>人物 · {loading ? "加载中…" : <><span className="num">{speakers.length}</span> 人</>}</span></summary>
        {loading ? <p role="status">正在加载人物名单…</p> : null}
        <label className="people-search">
          <span className="sr-only">搜索人物</span>
          <input
            type="search"
            placeholder="搜索人物"
            title="支持姓名、拼音和首字母"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <ul className="people-list" role="list">
        {visiblePeople.map((p) => (
          <li className="person-row" key={p.person_id} role="listitem">
            <span className="chip" style={{ background: speakerColor(p.person_id) }}>
              <Icon name="person" /> {p.display_name}
            </span>
            {p.is_self ? <span className="badge">本人</span> : null}
            {p.enrolled ? (
              <span className="person-enrolled" title="已登记声纹">
                <Icon name="check_circle" /> 已登记
              </span>
            ) : null}
            <span className="person-count muted" title="已标注 = 你确认的样本;已归 = 手动 + 声纹自动归段">
              已标注 <span className="num">{p.manual_count}</span> · 已归{" "}
              <span className="num">{p.attributed_count}</span> 段
            </span>
            <button
              className="ghost ghost-sm"
              onClick={() => void enroll.run(p.person_id)}
              disabled={enroll.pending || p.manual_count === 0}
              aria-busy={enroll.pending}
              title={
                p.manual_count === 0
                  ? "先在声纹图上框选并标注 TA 的片段(或用智能建议)"
                  : "从该人物的手动标注片段重新冻结声纹中心(标注时已自动登记)"
              }
            >
              {enroll.pending ? <span className="spinner" aria-hidden /> : <Icon name="mic" />}
              {p.enrolled ? "重新登记声纹" : "登记声纹"}
            </button>
            {p.person_type !== "unknown_voice" ? (
              <button type="button" className="ghost ghost-sm" aria-label={`改名 ${p.display_name}`}
                disabled={rename.pending} onClick={() => { setEditingPersonId(p.person_id); setEditName(p.display_name); setEditError(null); }}>
                改名
              </button>
            ) : null}
            {deleteButton(p)}
            {editingPersonId === p.person_id ? (
              <form className="person-rename-form" onSubmit={(e) => { e.preventDefault(); if (!hasDuplicate(editName, p.person_id)) void rename.run(p); }}>
                <input aria-label={`${p.display_name}的新名称`} value={editName} autoFocus disabled={rename.pending}
                  aria-invalid={!!editError || hasDuplicate(editName, p.person_id)}
                  onChange={(e) => { setEditName(e.target.value); setEditError(null); }}
                  onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setEditingPersonId(null); setEditError(null); } }} />
                <button type="submit" className="primary" disabled={rename.pending || !editName.trim() || hasDuplicate(editName, p.person_id)}>
                  {rename.pending ? "正在保存…" : "保存"}
                </button>
                <button type="button" className="ghost" disabled={rename.pending} onClick={() => { setEditingPersonId(null); setEditError(null); }}>取消</button>
                {hasDuplicate(editName, p.person_id) || editError ? <p className="person-name-error" role="alert">{hasDuplicate(editName, p.person_id) ? "人物名称已存在，请使用其他名称" : editError}</p> : null}
              </form>
            ) : null}
          </li>
        ))}
        {people.length === 0 && !loadError && !loading ? <li className="muted">暂无人物</li> : null}
        {people.length > 0 && visiblePeople.length === 0 && !loading ? <li className="muted">没有匹配人物</li> : null}
        </ul>
      </details>

      <details className="people-management">
        <summary>
          <span>管理工具</span>
          <span className="muted">新建 / 噪音 / 建议 / 补漏</span>
        </summary>
        <div className="people-management-body">
      {/* 非发言人 (噪音/多人/无效): labelable buckets, NOT voiceprint identities — no 登记声纹.
          A one-tap "+ 噪音/多人 类别" seeds the first bucket when none exists yet. */}
      <div className="people-nonspeakers">
        <div className="people-nonspeakers-head">
          <span className="section-subtitle">非发言人(噪音/多人)</span>
          {hasNonSpeaker ? null : (
            <button
              className="ghost ghost-sm"
              onClick={() => void createNonSpeaker.run()}
              disabled={createNonSpeaker.pending}
              aria-busy={createNonSpeaker.pending}
              title="新建一个「噪音/多人」类别,用于在图上把杂乱的点标为噪音"
            >
              {createNonSpeaker.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
              + 噪音/多人 类别
            </button>
          )}
        </div>
        {hasNonSpeaker ? (
          <ul className="people-list people-nonspeaker-list" role="list">
            {nonSpeakers.map((p) => (
              <li className="person-row person-nonspeaker" key={p.person_id} role="listitem">
                <span className="chip chip-noise" title="非发言人(噪音/多人)">
                  <Icon name="noise" /> {p.display_name}
                </span>
                <span className="badge badge-noise">非发言人</span>
                <span className="person-count muted" title="已归 = 标为噪音/多人的片段数">
                  已归 <span className="num">{p.attributed_count}</span> 段
                </span>
                {deleteButton(p)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted people-nonspeaker-hint">
            把噪音、多人重叠或无效的片段框选后标到这里,它们就不会冒充真实发言人。
          </p>
        )}
        {hasNonSpeaker ? (
          <div className="noise-tools">
            <button
              className="ghost ghost-sm"
              onClick={() => void markNoise.run("filler")}
              disabled={markNoise.pending}
              aria-busy={markNoise.pending}
              title="把纯语气词(嗯/啊/呃/哦…)的片段一键标为噪音,不动已标注的真实发言人"
            >
              {markNoise.pending ? <span className="spinner" aria-hidden /> : <Icon name="noise" />}
              一键标注语气词
            </button>
            <span className="noise-short">
              <button
                className="ghost ghost-sm"
                onClick={() => void markNoise.run("short")}
                disabled={markNoise.pending}
                aria-busy={markNoise.pending}
                title={`把短于 ${noiseMs}ms 的片段标为噪音(已标噪音段中位时长约 240ms)`}
              >
                标注短段
              </button>
              <input
                type="number"
                aria-label="噪音时长阈值(毫秒)"
                className="noise-ms"
                min={50}
                max={2000}
                step={50}
                value={noiseMs}
                onChange={(e) => setNoiseMs(Number(e.target.value) || 0)}
                disabled={markNoise.pending}
              />
              <span className="muted">ms 以下</span>
            </span>
            <label className="muted noise-scope-note">噪音处理范围:
              <select aria-label="噪音处理范围" value={sessionId ? scope : "all"} onChange={(e) => setScope(e.target.value as "all" | "session")}>
                <option value="all">{day ? "当天" : "全库"}</option><option value="session" disabled={!sessionId}>本会话</option>
              </select>
            </label>
          </div>
        ) : null}
      </div>

      <div className="people-add">
        <input
          aria-label="新建人物"
          value={newName}
          onChange={(e) => { setNewName(e.target.value); setCreateError(null); }}
          placeholder="新建人物"
          disabled={create.pending}
        />
        <button
          className="ghost"
          onClick={() => newName && void create.run(newName)}
          disabled={create.pending || !newName.trim() || createDuplicate}
          aria-busy={create.pending}
        >
          {create.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
          {create.pending ? "正在新建…" : "新建人物"}
        </button>
        {createDuplicate || createError ? <p className="person-name-error" role="alert">{createDuplicate ? "人物名称已存在，请使用其他名称" : createError}</p> : null}
      </div>

      {/* Smart suggestions — only meaningful with a selected session. */}
      <div className="people-suggest">
        <div className="people-suggest-head">
          <span className="section-subtitle">智能建议</span>
          <Button
            variant="primary"
            icon="viewpoint"
            busy={suggest.pending}
            onClick={() => void suggest.run()}
            disabled={!sessionId}
            title={sessionId ? "为此会话的每个聚类匹配最相近的已登记人物" : "请先选择一个会话"}
          >
            智能建议
          </Button>
        </div>
        {!sessionId ? (
          <p className="muted">选择一个会话后,可为其聚类匹配已登记人物。</p>
        ) : suggestions === null ? null : suggestions.length === 0 ? (
          <p className="muted">没有可用建议(可能尚无已登记声纹)。</p>
        ) : (
          <ul className="suggestion-list" role="list">
            {suggestions.map((s) => (
              <li className="suggestion-row" key={s.speaker} role="listitem">
                <span className="chip" style={{ background: speakerColor(s.speaker) }}>{s.speaker}</span>
                <Icon name="chevron" />
                <span className="suggestion-person">{s.person_label}</span>
                <span className="confidence-chip" title="声纹相似度">{s.score.toFixed(2)}</span>
                <button
                  className="ghost"
                  onClick={() => void adopt.run(s)}
                  disabled={adopt.pending}
                  aria-busy={adopt.pending}
                  title={`将 ${s.speaker} 标注为 ${s.person_label}`}
                >
                  <Icon name="accept" /> 采用
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

        </div>
      </details>
    </InspectorPanel>
  );
}
