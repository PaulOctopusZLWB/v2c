from __future__ import annotations

from pathlib import Path

import uvicorn

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
    uvicorn.run(create_app(config=config), host=host, port=port)
