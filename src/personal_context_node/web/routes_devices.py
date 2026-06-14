from __future__ import annotations

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.device_discovery import discover_import_sources


router = APIRouter(prefix="/api")


@router.get("/devices")
def devices(request: Request) -> dict[str, object]:
    # audio_count is "files present on the source" — it is not de-duplicated
    # against already-imported files (DB cross-check is out of scope for v1).
    config: AppConfig = request.app.state.config
    return {"sources": discover_import_sources(config=config)}
