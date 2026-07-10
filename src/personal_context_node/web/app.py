from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from personal_context_node.config import AppConfig
from personal_context_node.web.routes_audio import router as audio_router
from personal_context_node.web.routes_devices import router as devices_router
from personal_context_node.web.routes_dynamics import router as dynamics_router
from personal_context_node.web.routes_home import router as home_router
from personal_context_node.web.routes_identity import router as identity_router
from personal_context_node.web.routes_llm import router as llm_router
from personal_context_node.web.routes_clusters import router as clusters_router
from personal_context_node.web.routes_memory import router as memory_router
from personal_context_node.web.routes_pipeline import events_router, router as pipeline_router
from personal_context_node.web.routes_settings import router as settings_router
from personal_context_node.web.routes_speakers import router as speakers_router
from personal_context_node.web.routes_status import router as status_router
from personal_context_node.web.routes_transcripts import router as transcripts_router
from personal_context_node.web.routes_triage import router as triage_router
from personal_context_node.web.routes_viewpoints import prompts_router, router as viewpoints_router
from personal_context_node.web.worker import PipelineWorker


def create_app(*, config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        yield
        # The worker keeps model adapters (funasr_server subprocess) resident across
        # drains; release them when the app stops so no orphan model process lingers.
        app.state.worker.request_stop()
        app.state.worker.close_adapters()

    app = FastAPI(title="Personal Context Node Control Panel", lifespan=_lifespan)
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
    app.include_router(home_router)
    app.include_router(transcripts_router)
    app.include_router(viewpoints_router)
    app.include_router(prompts_router)
    app.include_router(speakers_router)
    app.include_router(identity_router)
    app.include_router(triage_router)
    app.include_router(dynamics_router)
    app.include_router(audio_router)
    app.include_router(llm_router)
    app.include_router(memory_router)
    app.include_router(clusters_router)
    app.include_router(devices_router)
    app.include_router(settings_router)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"app": "Personal Context Node", "mode": "api-only"}

    dist_dir = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist_dir.exists():
        app.mount("/app", StaticFiles(directory=dist_dir, html=True), name="frontend")

    return app
