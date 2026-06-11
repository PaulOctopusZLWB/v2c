from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig


def assert_personal_context_vault(config: AppConfig) -> None:
    vault = config.obsidian_vault.expanduser()
    if _is_supcon_vault(vault):
        raise ValueError(f"refusing to write PersonalContext notes into Supcon vault: {vault}")


def _is_supcon_vault(path: Path) -> bool:
    return path.name == "Supcon"
