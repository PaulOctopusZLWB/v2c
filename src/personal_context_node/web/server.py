from __future__ import annotations

from pathlib import Path

import uvicorn

from personal_context_node.speaker_embeddings import warm_projection_engine
from personal_context_node.web.app import create_app
from personal_context_node.web.config import load_web_config


def run_web_server(
    *,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    if host != "127.0.0.1":
        raise ValueError("web server v1 must bind to 127.0.0.1")
    config = load_web_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    # Pre-JIT the UMAP/numba stack while uvicorn boots so the first voiceprint-map request
    # doesn't pay the ~10s import + compile cost. Daemon thread; only the real server does this
    # (tests build apps via create_app and must not spawn warmup CPU load).
    warm_projection_engine()
    uvicorn.run(create_app(config=config), host=host, port=port)
