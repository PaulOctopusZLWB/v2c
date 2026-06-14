from __future__ import annotations

from fastapi import FastAPI

from personal_context_node.config import AppConfig
from personal_context_node.web.routes_pipeline import events_router, router as pipeline_router
from personal_context_node.web.routes_status import router as status_router
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
            "require_accepted_transcripts": bool(getattr(config, "require_accepted_transcripts", False)),
        }

    app.state.worker = PipelineWorker(config=config)
    app.include_router(status_router)
    app.include_router(pipeline_router)
    app.include_router(events_router)  # serves GET /api/events

    return app
