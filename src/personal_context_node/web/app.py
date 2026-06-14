from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from personal_context_node.config import AppConfig
from personal_context_node.web.routes_audio import router as audio_router
from personal_context_node.web.routes_llm import router as llm_router
from personal_context_node.web.routes_pipeline import events_router, router as pipeline_router
from personal_context_node.web.routes_speakers import router as speakers_router
from personal_context_node.web.routes_status import router as status_router
from personal_context_node.web.routes_transcripts import router as transcripts_router
from personal_context_node.web.worker import PipelineWorker


def create_app(*, config: AppConfig) -> FastAPI:
    app = FastAPI(title="Personal Context Node Control Panel")
    app.state.config = config

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "host": "127.0.0.1",
            "data_dir": str(config.data_dir),
            "obsidian_vault": str(config.obsidian_vault),
            "require_accepted_transcripts": bool(config.require_accepted_transcripts),
        }

    app.state.worker = PipelineWorker(config=config)
    app.include_router(status_router)
    app.include_router(pipeline_router)
    app.include_router(events_router)  # serves GET /api/events
    app.include_router(transcripts_router)
    app.include_router(speakers_router)
    app.include_router(audio_router)
    app.include_router(llm_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"app": "Personal Context Node", "mode": "api-only"}

    dist_dir = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist_dir.exists():
        app.mount("/app", StaticFiles(directory=dist_dir, html=True), name="frontend")

    return app
