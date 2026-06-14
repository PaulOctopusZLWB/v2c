from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig


def load_web_config(*, config_path: Path | None, data_dir: Path | None, obsidian_vault: Path | None) -> AppConfig:
    if config_path is not None:
        return AppConfig.from_toml(config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    overrides: dict[str, object] = {}
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if obsidian_vault is not None:
        overrides["obsidian_vault"] = obsidian_vault
    return AppConfig(**overrides)
