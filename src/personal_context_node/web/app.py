from __future__ import annotations

from fastapi import FastAPI

from personal_context_node.config import AppConfig


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

    return app
