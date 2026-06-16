from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.settings import effective_settings, write_settings


router = APIRouter(prefix="/api/settings")


class SettingsUpdate(BaseModel):
    asr_mode: str | None = None
    asr_preset_spk_num: int | None = None
    glm_model: str | None = None
    glm_base_url: str | None = None
    glm_thinking: bool | None = None


@router.get("")
def get_settings_route(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return effective_settings(config)


@router.put("")
def update_settings_route(request: Request, payload: SettingsUpdate) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        write_settings(config, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return effective_settings(config)
