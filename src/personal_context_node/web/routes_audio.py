from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from personal_context_node.config import AppConfig
from personal_context_node.transcription import segment_audio_path


router = APIRouter(prefix="/api/audio")


@router.get("/segments/{segment_id}")
def segment_audio(request: Request, segment_id: str) -> FileResponse:
    config: AppConfig = request.app.state.config
    path = segment_audio_path(config=config, segment_id=segment_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"segment audio not found: {segment_id}")
    return FileResponse(path, media_type="audio/wav")
