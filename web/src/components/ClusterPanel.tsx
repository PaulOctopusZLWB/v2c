import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Person, SpeakerCluster } from "../api/types";
import { t } from "../i18n";
import { speakerColor } from "../lib/speakerColors";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { useSegmentAudio } from "../hooks/useSegmentAudio";
import { Icon } from "./Icon";

/**
 * 声纹聚类 — list a day's diarization clusters (`spk_NN`), each with a sample, its current
 * person mapping, and a checkbox. Over-clustering is fixed by selecting the extra clusters,
 * picking one person, and "合并/指派为同一人" (bulk-assign → the attribution view collapses
 * them to one person). Refreshes the list after a successful merge.
 */
export function ClusterPanel({
  day,
  persons,
  onCreatePerson,
  onPlaybackError
}: {
  day: string;
  persons: Person[];
  onCreatePerson: (displayName: string) => Promise<void>;
  onPlaybackError?: (message: string) => void;
}) {
  const [clusters, setClusters] = useState<SpeakerCluster[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [personId, setPersonId] = useState("");
  const [newName, setNewName] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const audio = useSegmentAudio();

  async function load() {
    setLoadError(null);
    try {
      const result = await api.speakerClusters(day);
      setClusters(result.clusters ?? []);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "加载失败");
    }
  }

  // Reload when the day changes; clear any stale selection so it never spans days.
  useEffect(() => {
    setSelected(new Set());
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day]);

  function toggle(clusterId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(clusterId)) next.delete(clusterId);
      else next.add(clusterId);
      return next;
    });
  }

  const merge = useAsyncAction(async () => {
    const speakers = clusters.map((c) => c.speaker_cluster_id).filter((id) => selected.has(id));
    if (speakers.length === 0 || !personId) return;
    await api.assignPersonBulk(speakers, personId);
    setSelected(new Set());
    await load();
  });

  const create = useAsyncAction(async (name: string) => {
    await onCreatePerson(name);
    setNewName("");
  });

  const play = (segmentId: string) => {
    void audio
      .play(segmentId)
      .catch((err) => onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed"));
  };

  return (
    <section className="cluster-panel card">
      <div className="section-title">
        <Icon name="person" /> {t.cluster.title}
      </div>

      {loadError ? <p className="muted" role="alert">{loadError}</p> : null}
      {!loadError && clusters.length === 0 ? <p className="muted">{t.cluster.empty}</p> : null}

      {clusters.map((c) => (
        <div className="cluster-row" key={c.speaker_cluster_id}>
          <label className="cluster-select">
            <input
              type="checkbox"
              aria-label={c.speaker_cluster_id}
              checked={selected.has(c.speaker_cluster_id)}
              onChange={() => toggle(c.speaker_cluster_id)}
            />
            <span className="chip" style={{ background: speakerColor(c.speaker_cluster_id) }}>
              <Icon name="person" /> {c.speaker_cluster_id}
            </span>
          </label>
          <p className="cluster-sample">{c.sample_text}</p>
          <div className="cluster-meta">
            <span className="num">{c.segment_count}</span> {t.cluster.segments}
            <span className={`badge${c.person_label ? " s-accepted" : ""}`}>
              {c.person_label ?? t.cluster.unassigned}
            </span>
            <button
              className="icon-btn ghost"
              aria-label={`${t.cluster.listen} ${c.speaker_cluster_id}`}
              title={t.cluster.listen}
              onClick={() => play(c.sample_segment_id)}
            >
              <Icon name="play" /> {t.cluster.listen}
            </button>
          </div>
        </div>
      ))}

      <div className="cluster-merge">
        <select
          aria-label={t.cluster.pickPerson}
          value={personId}
          disabled={merge.pending}
          onChange={(e) => setPersonId(e.target.value)}
        >
          <option value="" disabled>{t.cluster.pickPerson}…</option>
          {persons.map((p) => (
            <option key={p.person_id} value={p.person_id}>{p.display_name}</option>
          ))}
        </select>
        <button
          className="primary"
          onClick={() => void merge.run()}
          disabled={merge.pending || selected.size === 0 || !personId}
          aria-busy={merge.pending}
        >
          {merge.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
          {merge.pending ? t.cluster.merging : t.cluster.merge}
        </button>
      </div>

      <div className="cluster-add">
        <input
          aria-label={t.cluster.newPerson}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t.cluster.newPerson}
          disabled={create.pending}
        />
        <button
          className="ghost"
          onClick={() => newName && void create.run(newName)}
          disabled={create.pending || !newName}
          aria-busy={create.pending}
        >
          {create.pending ? <span className="spinner" aria-hidden /> : <Icon name="person" />}
          {create.pending ? "正在新建…" : t.cluster.newPerson}
        </button>
      </div>
    </section>
  );
}
