import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PersonRow, Suggestion } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";
import { Button, InspectorPanel, StatusBadge } from "../../components/ui";

/**
 * 人物 — the supervised-identity surface. The voiceprint (not the diarizer's unreliable spk_NN)
 * is the global identity signal, so the loop is: label a few segments as a person (ground truth) →
 * 全局识别 propagates that voiceprint to every segment in every session → each person stays
 * consistent across sessions, and the more you label, the sharper the boundary.
 *  - 智能建议 (suggest): for the selected session, score each diarization cluster against the
 *    enrolled centroids; one tap (采用) labels that whole cluster's segments as the person.
 *  - 登记声纹 (enroll): mostly automatic now (labeling auto-enrolls). Re-freezes the centroid from
 *    a person's manual labels — disabled until they have at least one manual label.
 *  - 全局识别 (auto-attribute): re-enroll everyone from their manual labels, then assign every
 *    non-manual segment to the nearest person voiceprint ≥ threshold, never overwriting manuals.
 * Every mutation calls onChanged() so the map (and its colours) refetch.
 */
export function PeoplePanel({
  sessionId,
  day,
  onChanged,
  push,
  pushAction,
  onAutoAttributed
}: {
  sessionId?: string | null;
  day?: string | null;
  onChanged: () => void;
  push: (title: string, message?: string) => void;
  pushAction: (message: string, actionLabel: string, onAction: () => void) => void;
  onAutoAttributed?: (count: number) => void;
}) {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [threshold, setThreshold] = useState(0.6);
  // Identity scope: 全部 (global, cross-session — the default and the whole point) vs 本会话.
  const [scope, setScope] = useState<"all" | "session">("all");

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setPeople((await api.people()).people ?? []);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // A mutation succeeded: reload the local roster AND let the parent refetch the map.
  const refresh = useCallback(async () => {
    await load();
    onChanged();
  }, [load, onChanged]);

  const create = useAsyncAction(async (name: string) => {
    await api.createPerson(name);
    setNewName("");
    // refresh() (not just load()) so the parent's persons roster — and the assign-person
    // <select> fed by it — picks up the new person immediately, not just this panel's list.
    await refresh();
  });

  // One-tap 噪音/多人 bucket: create a ready-to-use non_speaker person so the user can box-select
  // messy points on the map and mark them noise. Only offered when no non_speaker exists yet.
  const createNonSpeaker = useAsyncAction(async () => {
    try {
      await api.createPerson("噪音/多人", "non_speaker");
      push("已创建「噪音/多人」类别");
      await refresh();
    } catch (err) {
      push("创建失败", err instanceof Error ? err.message : undefined);
    }
  });

  // Delete an accidental duplicate person: confirm, cascade-delete on the backend, then reload
  // the roster + recolor the map. Never offered for 本人 (is_self) — the button is hidden for them.
  const remove = useAsyncAction(async (p: PersonRow) => {
    if (!window.confirm(`删除人物「${p.display_name}」?其声纹与归属将被清除。`)) return;
    try {
      await api.deletePerson(p.person_id);
      push(`已删除人物「${p.display_name}」`);
      await refresh();
    } catch (err) {
      push("删除失败", err instanceof Error ? err.message : undefined);
    }
  });

  const enroll = useAsyncAction(async (personId: string) => {
    try {
      const res = await api.enrollPerson(personId);
      push(`已登记 ${res.n_segments} 段声纹`);
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
      push(`已将 ${s.speaker} 标注为 ${s.person_label}`, `${res.labeled} 段`);
      // Drop the adopted suggestion so the row can't be double-applied.
      setSuggestions((cur) => (cur ?? []).filter((x) => x.speaker !== s.speaker));
      await refresh();
    } catch (err) {
      push("采用失败", err instanceof Error ? err.message : undefined);
    }
  });

  // 全局识别 — re-enroll everyone from their manual labels, then assign every non-manual segment
  // to the nearest person voiceprint ≥ threshold (manual labels are never overwritten). Default
  // scope 全部 runs over ALL sessions (the consistent cross-session identity); 本会话 scopes it.
  const autoAttribute = useAsyncAction(async () => {
    const useSession = scope === "session" && !!sessionId;
    try {
      const res = await api.autoAttribute({
        session_id: useSession ? sessionId : null,
        day: null,
        threshold
      });
      const dist = Object.entries(res.per_person)
        .map(([id, n]) => `${people.find((p) => p.person_id === id)?.display_name ?? id} ${n}`)
        .join(" · ");
      pushAction(
        `已识别 ${res.assigned}/${res.total} 段(未定 ${res.unassigned})`,
        "查看",
        () => push("识别分布", dist || "无")
      );
      onAutoAttributed?.(res.assigned);
      await refresh();
    } catch (err) {
      // The backend 400s with "no enrolled people" when nobody is enrolled — map to a friendly hint.
      const msg = err instanceof Error ? err.message : undefined;
      push("全局识别失败", msg?.includes("400") ? "请先标注并登记至少一个人物的声纹" : msg);
    }
  });

  // Split the roster: real voiceprint identities vs. the 非发言人 (噪音/多人/无效) buckets, which
  // are labelable but NOT a voiceprint (no 登记声纹, rendered in a muted group at the bottom).
  const speakers = people.filter((p) => p.person_type !== "non_speaker");
  const nonSpeakers = people.filter((p) => p.person_type === "non_speaker");
  const hasNonSpeaker = nonSpeakers.length > 0;
  const normalizedQuery = query.trim().toLowerCase();
  const visiblePeople = speakers.filter((p) =>
    normalizedQuery.length === 0 ? true : p.display_name.toLowerCase().includes(normalizedQuery)
  );

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
      subtitle="声纹登记、归属计数、智能建议与全局识别"
      actions={
        <StatusBadge status="info">
          <span className="num">{speakers.length}</span> 人
        </StatusBadge>
      }
      className="people-panel"
    >
      <p className="people-explainer muted">
        标注几段是谁 → 全局识别 → 每个人在所有会话里一致;标得越多,边界越准。
      </p>

      <label className="people-search">
        <span className="sr-only">搜索人物</span>
        <input
          type="search"
          placeholder="搜索人物"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>

      {loadError ? <p className="muted" role="alert">{loadError}</p> : null}

      <details className="people-roster" open>
        <summary>人物 · <span className="num">{speakers.length}</span> 人(点击折叠)</summary>
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
            {deleteButton(p)}
          </li>
        ))}
        {people.length === 0 && !loadError ? <li className="muted">暂无人物</li> : null}
        {people.length > 0 && visiblePeople.length === 0 ? <li className="muted">没有匹配人物</li> : null}
        </ul>
      </details>

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
      </div>

      <div className="people-add">
        <input
          aria-label="新建人物"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="新建人物"
          disabled={create.pending}
        />
        <button
          className="ghost"
          onClick={() => newName && void create.run(newName)}
          disabled={create.pending || !newName}
          aria-busy={create.pending}
        >
          {create.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
          {create.pending ? "正在新建…" : "新建人物"}
        </button>
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

      {/* 全局识别 — assign every segment to the nearest enrolled voiceprint, manual labels kept. */}
      <div className="people-auto">
        <div className="people-auto-head">
          <span className="section-subtitle">全局识别(按声纹)</span>
          <span className="people-auto-helper muted">标注后点此重新识别(无需训练,即时生效)</span>
          <div className="people-scope" role="radiogroup" aria-label="识别范围">
            <span className="muted">范围:</span>
            <button
              type="button"
              role="radio"
              aria-checked={scope === "all"}
              className={`scope-btn${scope === "all" ? " active" : ""}`}
              onClick={() => setScope("all")}
              title="对所有会话统一识别(跨会话一致)"
            >
              全部
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={scope === "session"}
              className={`scope-btn${scope === "session" ? " active" : ""}`}
              onClick={() => setScope("session")}
              disabled={!sessionId}
              title={sessionId ? "仅对当前选中的会话识别" : "请先选择一个会话"}
            >
              本会话
            </button>
          </div>
        </div>
        <div className="people-auto-controls">
          <div className="people-threshold">
            <label htmlFor="people-threshold">归人阈值</label>
            <input
              id="people-threshold"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
            />
            <span className="num">{threshold.toFixed(2)}</span>
          </div>
          <Button
            variant="primary"
            icon="refresh"
            busy={autoAttribute.pending}
            onClick={() => void autoAttribute.run()}
            title="把每个片段归到声纹最相近的已登记人物(相似度需达阈值),你的手动标注始终保留"
          >
            全局识别(按声纹)
          </Button>
        </div>
      </div>
    </InspectorPanel>
  );
}
