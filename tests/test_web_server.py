from __future__ import annotations

from pathlib import Path

import pytest

from personal_context_node.web import server


def test_web_server_rejects_arbitrary_bind_host() -> None:
    with pytest.raises(ValueError, match="127.0.0.1 or 0.0.0.0"):
        server.run_web_server(
            config_path=None,
            data_dir=None,
            obsidian_vault=None,
            host="192.0.2.10",
        )


def test_web_server_allows_container_wildcard_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    config = object()

    monkeypatch.setattr(server, "load_web_config", lambda **_kwargs: config)
    monkeypatch.setattr(server, "warm_projection_engine", lambda: None)
    monkeypatch.setattr(server, "create_app", lambda **_kwargs: "app")
    monkeypatch.setattr(server.uvicorn, "run", lambda app, host, port: captured.update(app=app, host=host, port=port))

    server.run_web_server(
        config_path=tmp_path / "config.toml",
        data_dir=None,
        obsidian_vault=None,
        host="0.0.0.0",
        port=8765,
    )

    assert captured == {"app": "app", "host": "0.0.0.0", "port": 8765}
