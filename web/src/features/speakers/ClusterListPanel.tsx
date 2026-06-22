import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { Person, SpeakerCluster } from "../../api/types";
import { speakerColor } from "../../lib/speakerColors";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { Icon } from "../../components/Icon";
import { InspectorPanel, StatusBadge } from "../../components/ui";

/**
 * 声纹聚类 — the PRIMARY identification surface. Global voiceprint clusters (vp_*) are high-purity
 * (one cluster ≈ one person), so the fast path is: pick a person from each cluster's dropdown and the
 * WHOLE cluster is attributed globally. The map is the verification companion, not the main act.
 *  - 自动聚类 (auto-cluster): re-runs UMAP→HDBSCAN over the voiceprints into global vp_* groups.
 *    Destructive (rewrites speaker_cluster_id) — confirmed before running.
 *  - per row: vp chip + size + sample + person <select>; choosing a person labels every segment in
 *    the cluster (per-segment manual overrides, so it survives a re-cluster). onChanged() recolors the map.
 */
export function ClusterListPanel({
  onChanged,
  push,
}: {
  onChanged: () => void;
  push: (title: string, message?: string) => void;
}) {
  const [clusters, setClusters] = useState<SpeakerCluster[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [c, p] = await Promise.all([api.globalClusters(1), api.persons()]);
      setClusters(c.clusters ?? []);
      setPersons(p.persons ?? []);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const autoCluster = useAsyncAction(async () => {
    if (!window.confirm("自动聚类会按声纹重新分组(覆盖现有 vp_ 分组)。已手动标注的归属会保留。继续?")) return;
    try {
      const res = await api.autoCluster({});
      push("已自动聚类", `分出 ${res.clusters} 组 · 归组 ${res.assigned} 段 · 未归 ${res.unassigned}`);
      await load();
      onChanged();
    } catch (err) {
      push("自动聚类失败", err instanceof Error ? err.message : undefined);
    }
  });

  const assign = async (cluster: SpeakerCluster, personId: string) => {
    if (!personId) return;
    setBusyId(cluster.speaker_cluster_id);
    try {
      const res = await api.assignCluster(cluster.speaker_cluster_id, personId);
      const label = persons.find((p) => p.person_id === personId)?.display_name ?? personId;
      push(`已将 ${cluster.speaker_cluster_id} 归属至 ${label}`, `${res.labeled} 段`);
      // Optimistic: reflect the new person on the row immediately.
      setClusters((prev) =>
        prev.map((c) =>
          c.speaker_cluster_id === cluster.speaker_cluster_id
            ? { ...c, person_id: personId, person_label: label, labeled_count: c.segment_count }
            : c,
        ),
      );
      onChanged();
    } catch (err) {
      push("分配失败", err instanceof Error ? err.message : undefined);
    } finally {
      setBusyId(null);
    }
  };

  const total = clusters.length;
  const assigned = clusters.filter((c) => c.person_id).length;

  return (
    <InspectorPanel
      title="声纹聚类"
      subtitle="按大小排序,逐个选人即可把整组全局归属;图用于核对"
      actions={
        <StatusBadge status="info">
          <span className="num">{assigned}</span>/<span className="num">{total}</span> 已分配
        </StatusBadge>
      }
      className="people-panel cluster-panel"
    >
      <div className="cluster-tools">
        <button
          className="primary"
          onClick={() => void autoCluster.run()}
          disabled={autoCluster.pending}
          aria-busy={autoCluster.pending}
          title="按声纹重新分组(UMAP→HDBSCAN);会覆盖现有 vp_ 分组,手动标注保留"
        >
          {autoCluster.pending ? <span className="spinner" aria-hidden /> : <Icon name="viewpoint" />}
          {autoCluster.pending ? "正在聚类…" : "自动聚类"}
        </button>
        <button className="ghost ghost-sm" onClick={() => void load()} title="刷新聚类列表">
          <Icon name="refresh" /> 刷新
        </button>
      </div>

      {loadError ? <p className="muted" role="alert">{loadError}</p> : null}

      {clusters.length === 0 && !loadError ? (
        <p className="muted">还没有声纹分组。先在工具栏「提取声纹」,再点上方「自动聚类」生成全局分组。</p>
      ) : (
        <ul className="cluster-list" role="list">
          {clusters.map((c) => (
            <li className="cluster-row" key={c.speaker_cluster_id} role="listitem">
              <div className="cluster-row-head">
                <span className="chip" style={{ background: speakerColor(c.person_id ?? c.speaker_cluster_id) }}>
                  <Icon name="person" /> {c.speaker_cluster_id}
                </span>
                <span className="confidence-chip" title="该组片段数">{c.segment_count} 段</span>
                {c.person_id ? (
                  <StatusBadge status="success" className="cluster-assigned">{c.person_label}</StatusBadge>
                ) : (
                  <span className="cluster-unassigned muted">未分配</span>
                )}
              </div>
              <p className="cluster-sample muted" title={c.sample_text ?? ""}>
                {c.sample_text ?? "(无样例文本)"}
              </p>
              <select
                aria-label={`分配 ${c.speaker_cluster_id}`}
                className="cluster-assign"
                value={c.person_id ?? ""}
                disabled={busyId === c.speaker_cluster_id}
                onChange={(e) => void assign(c, e.target.value)}
              >
                <option value="">选择人物…</option>
                {persons.map((p) => (
                  <option key={p.person_id} value={p.person_id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
            </li>
          ))}
        </ul>
      )}
    </InspectorPanel>
  );
}
