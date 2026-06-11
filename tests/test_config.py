from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig


def test_app_config_loads_local_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
[paths]
data_dir = "pcn-data"
obsidian_vault = "/vault"
nas_archive_root = "/nas"

[asr]
backend = "command"
command = "python scripts/funasr_wrapper.py"

[vad]
backend = "energy"
threshold = 0.02
max_chunk_ms = 45000

[llm]
backend = "rule_based"

[obsidian]
edit_grace_seconds = 45
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.data_dir == tmp_path / "config" / "pcn-data"
    assert config.obsidian_vault == Path("/vault")
    assert config.nas_archive_root == Path("/nas")
    assert config.asr_backend == "command"
    assert config.asr_command == "python scripts/funasr_wrapper.py"
    assert config.vad_backend == "energy"
    assert config.vad_threshold == 0.02
    assert config.max_chunk_ms == 45000
    assert config.llm_backend == "rule_based"
    assert config.edit_grace_seconds == 45


def test_app_config_with_overrides_keeps_explicit_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text("[paths]\ndata_dir = 'data'\nobsidian_vault = '/vault'\n", encoding="utf-8")

    config = AppConfig.from_toml(
        config_path,
        data_dir=tmp_path / "override-data",
        obsidian_vault=tmp_path / "override-vault",
    )

    assert config.data_dir == tmp_path / "override-data"
    assert config.obsidian_vault == tmp_path / "override-vault"
