"""AI 猜测:一个声纹聚类最可能属于谁 (design handoff Phase 6, GET /api/clusters/{id}/suggestion).

聚类内已有 embedding 的段取均值(L2 归一),与每个已登记人物的 voiceprint 质心做
余弦相似度,返回最高分的人选。纯只读;没有质心或聚类无 embedding 时返回 null 建议。
"""

from __future__ import annotations

import numpy as np

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import _non_speaker_person_ids, get_embeddings, get_person_centroids
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def cluster_suggestion(*, config: AppConfig, cluster_id: str) -> dict[str, object] | None:
    """None = unknown cluster;否则 {cluster_id, segment_count, embedded_count, suggestion}。"""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select segment_id from transcript_segments
            where coalesce(speaker_cluster_id, speaker) = ? and is_active = 1
            """,
            (cluster_id,),
        )
        persons = {
            str(r["person_id"]): str(r["display_name"])
            for r in fetch_all(conn, "select person_id, display_name from persons")
        }
    finally:
        conn.close()
    if not rows:
        return None

    segment_ids = [str(r["segment_id"]) for r in rows]
    embeddings = get_embeddings(config=config, segment_ids=segment_ids)
    centroids = get_person_centroids(config=config)
    payload: dict[str, object] = {
        "cluster_id": cluster_id,
        "segment_count": len(segment_ids),
        "embedded_count": len(embeddings),
        "suggestion": None,
    }
    if not embeddings or not centroids:
        return payload

    mean = np.mean(np.stack([np.asarray(v, dtype=np.float64) for v in embeddings.values()]), axis=0)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        return payload
    mean = mean / norm

    # 噪音/多人(non_speaker)是噪声类,不是真实声纹身份 —— 绝不作为聚类的猜测人选
    # (与 speaker_embeddings.suggest_people_for_session 一致)。
    noise_ids = _non_speaker_person_ids(config=config)
    best_person: str | None = None
    best_score = -1.0
    for person_id, centroid in centroids.items():
        if person_id in noise_ids:
            continue
        score = float(np.dot(mean, centroid))
        if score > best_score:
            best_score = score
            best_person = person_id
    if best_person is None:
        return payload
    payload["suggestion"] = {
        "person_id": best_person,
        "person_label": persons.get(best_person, best_person),
        "score": round(best_score, 4),
    }
    return payload
