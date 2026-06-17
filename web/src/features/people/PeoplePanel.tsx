import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PersonRow, Suggestion } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";

/**
 * 人物 — "People taught once". Lists every person with their voiceprint enrollment + how many
 * segments are attributed to them, and turns the voiceprint map into a teaching surface:
 *  - 登记声纹 (enroll): freeze a person's currently-attributed segments into a centroid.
 *  - 智能建议 (suggest): for the selected session, score each diarization cluster against the
 *    enrolled centroids; one tap (采用) labels that whole cluster's segments as the person.
 *  - 自动归人 (auto-attribute): label every in-scope segment whose voiceprint clears a threshold.
 * Every mutation calls onChanged() so the map (and its colours) refetch.
 */
export function PeoplePanel({
  sessionId,
  day,
  onChanged,
  push,
  pushAction
}: {
  sessionId?: string | null;
  day?: string | null;
  onChanged: () => void;
  push: (title: string, message?: string) => void;
  pushAction: (message: string, actionLabel: string, onAction: () => void) => void;
}) {
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [threshold, setThreshold] = useState(0.6);

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
    await load();
  });

  const enroll = useAsyncAction(async (personId: string) => {
    try {
      const res = await api.enrollPerson(personId);
      push(`已登记 ${res.n_segments} 段声纹`);
      await refresh();
    } catch (err) {
      push("登记失败", err instanceof Error ? err.message : undefined);
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

  const autoAttribute = useAsyncAction(async () => {
    try {
      const res = await api.autoAttribute({ session_id: sessionId ?? null, day: sessionId ? null : day ?? null, threshold });
      const dist = Object.entries(res.per_person)
        .map(([id, n]) => `${people.find((p) => p.person_id === id)?.display_name ?? id} ${n}`)
        .join(" · ");
      pushAction(
        `已归人 ${res.assigned}/${res.total}(未定 ${res.unassigned})`,
        "查看",
        () => push("归人分布", dist || "无")
      );
      await refresh();
    } catch (err) {
      push("自动归人失败", err instanceof Error ? err.message : undefined);
    }
  });

  return (
    <section className="people-panel card">
      <div className="section-title">
        <Icon name="person" /> 人物 — 教一次,处处认得
      </div>

      {loadError ? <p className="muted" role="alert">{loadError}</p> : null}

      <ul className="people-list" role="list">
        {people.map((p) => (
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
            <span className="person-count muted">
              已归 <span className="num">{p.attributed_count}</span> 段
            </span>
            <button
              className="ghost"
              onClick={() => void enroll.run(p.person_id)}
              disabled={enroll.pending}
              aria-busy={enroll.pending}
              title="将该人物当前已归段落冻结为声纹中心"
            >
              {enroll.pending ? <span className="spinner" aria-hidden /> : <Icon name="mic" />}
              {p.enrolled ? "重新登记声纹" : "登记声纹"}
            </button>
          </li>
        ))}
        {people.length === 0 && !loadError ? <li className="muted">暂无人物</li> : null}
      </ul>

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
          <button
            className="primary"
            onClick={() => void suggest.run()}
            disabled={suggest.pending || !sessionId}
            aria-busy={suggest.pending}
            title={sessionId ? "为此会话的每个聚类匹配最相近的已登记人物" : "请先选择一个会话"}
          >
            {suggest.pending ? <span className="spinner" aria-hidden /> : <Icon name="viewpoint" />}
            {suggest.pending ? "正在匹配…" : "智能建议"}
          </button>
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

      {/* One-tap auto-attribution across the current scope. */}
      <div className="people-auto">
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
        <button
          className="primary"
          onClick={() => void autoAttribute.run()}
          disabled={autoAttribute.pending}
          aria-busy={autoAttribute.pending}
          title="将范围内每个相似度达标的声纹自动归到对应人物"
        >
          {autoAttribute.pending ? <span className="spinner" aria-hidden /> : <Icon name="refresh" />}
          {autoAttribute.pending ? "正在归人…" : "自动归人(≥阈值)"}
        </button>
      </div>
    </section>
  );
}
