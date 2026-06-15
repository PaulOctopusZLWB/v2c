from __future__ import annotations

from pathlib import Path


def test_start_web_builds_frontend_when_dist_missing() -> None:
    script = Path("scripts/start-web.sh").read_text(encoding="utf-8")

    assert "web/dist/index.html" in script
    assert "npm --prefix web run build" in script
    assert "npm --prefix web install" in script
    assert "exit 1" in script
