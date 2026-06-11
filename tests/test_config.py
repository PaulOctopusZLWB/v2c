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
raw_audio_dir = "audio/inbox"
work_audio_dir = "audio/scratch"
sqlite_path = "state/custom.sqlite"
obsidian_vault = "/vault"
nas_archive_root = "/nas"
identity_dir = "identity-store"

[identity]
owner_did = "did:key:configured-owner"
signing_key_path = "keys/configured_ed25519.key"

[asr]
backend = "command"
command = "python scripts/funasr_wrapper.py"

[vad]
backend = "energy"
threshold = 0.02
max_chunk_ms = 45000

[llm]
backend = "rule_based"
send_person_names = false
send_speaker_labels = false
max_chunk_tokens = 4096

[obsidian]
edit_grace_seconds = 45
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.data_dir == tmp_path / "config" / "pcn-data"
    assert config.raw_audio_dir == tmp_path / "config" / "audio" / "inbox"
    assert config.work_audio_dir == tmp_path / "config" / "audio" / "scratch"
    assert config.database_path == tmp_path / "config" / "state" / "custom.sqlite"
    assert config.obsidian_vault == Path("/vault")
    assert config.nas_archive_root == Path("/nas")
    assert config.identity_dir == tmp_path / "config" / "identity-store"
    assert config.owner_did == "did:key:configured-owner"
    assert config.signing_key_path == tmp_path / "config" / "keys" / "configured_ed25519.key"
    assert config.asr_backend == "command"
    assert config.asr_command == "python scripts/funasr_wrapper.py"
    assert config.vad_backend == "energy"
    assert config.vad_threshold == 0.02
    assert config.max_chunk_ms == 45000
    assert config.llm_backend == "rule_based"
    assert config.send_person_names is False
    assert config.send_speaker_labels is False
    assert config.max_chunk_tokens == 4096
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
