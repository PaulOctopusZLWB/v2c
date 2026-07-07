"""聚类建议 API (design handoff Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from personal_context_node.cluster_suggestion import cluster_suggestion
from personal_context_node.config import AppConfig

router = APIRouter(prefix="/api")


@router.get("/clusters/{cluster_id}/suggestion")
def get_cluster_suggestion(cluster_id: str, request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    payload = cluster_suggestion(config=config, cluster_id=cluster_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown cluster: {cluster_id}")
    return payload
