from __future__ import annotations

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.home_overview import home_overview


router = APIRouter(prefix="/api/home")


@router.get("/overview")
def home_overview_route(request: Request) -> dict[str, object]:
    """The 首页 (home) dashboard payload: review backlog, people, coverage, recent sessions."""
    config: AppConfig = request.app.state.config
    return home_overview(config=config)
