from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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
device = "cpu"
language = "zh"
model_name = "sensevoice"
model_id = "iic/SenseVoiceSmall"
model_version = "sensevoice-local-2026-06"

[vad]
backend = "energy"
threshold = 0.02
model_id = "fsmn-vad"
model_revision = "v2.0.4"
min_speech_ms = 250
merge_gap_ms = 750
max_chunk_ms = 45000
chunk_overlap_ms = 500

[audio]
target_sample_rate_hz = 16000
target_channels = 1
target_sample_format = "s16"

[llm]
backend = "rule_based"
command = "python scripts/llm_wrapper.py"
send_person_names = false
send_speaker_labels = false
max_chunk_tokens = 4096

[obsidian]
edit_grace_seconds = 45

[tasks]
lease_seconds = 120
max_retries = 2

[archive]
backend = "command"
command = "rsync -a {source_path} {archive_path}"

[session]
session_gap_minutes = 45
cross_midnight_policy = "start_date"

[device.dji_mic_3]
enabled = true
volume_root = "Volumes"
root_path = "fixtures/fake_dji"
volume_name_patterns = ["DJI*", "MIC*"]
audio_globs = ["**/*.WAV", "**/*.wav"]
stable_seconds = 10
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
    assert config.asr_device == "cpu"  # from_toml must propagate [asr].device, not just the default
    assert config.asr_language == "zh"
    assert config.asr_model_name == "sensevoice"
    assert config.asr_model_id == "iic/SenseVoiceSmall"
    assert config.asr_model_version == "sensevoice-local-2026-06"
    assert config.vad_backend == "energy"
    assert config.vad_threshold == 0.02
    assert config.vad_model_id == "fsmn-vad"
    assert config.vad_model_revision == "v2.0.4"
    assert config.min_speech_ms == 250
    assert config.merge_gap_ms == 750
    assert config.max_chunk_ms == 45000
    assert config.chunk_overlap_ms == 500
    assert config.audio.target_sample_rate_hz == 16000
    assert config.audio.target_channels == 1
    assert config.audio.target_sample_format == "s16"
    assert config.llm_backend == "rule_based"
    assert config.llm_command == "python scripts/llm_wrapper.py"
    assert config.send_person_names is False
    assert config.send_speaker_labels is False
    assert config.max_chunk_tokens == 4096
    assert config.edit_grace_seconds == 45
    assert config.task_lease_seconds == 120
    assert config.task_max_retries == 2
    assert config.archive_backend == "command"
    assert config.archive_command == "rsync -a {source_path} {archive_path}"
    assert config.session_gap_minutes == 45
    assert config.session_cross_midnight_policy == "start_date"
    assert config.dji_mic_3.enabled is True
    assert config.dji_mic_3.volume_root == tmp_path / "config" / "Volumes"
    assert config.dji_mic_3.root_path == tmp_path / "config" / "fixtures" / "fake_dji"
    assert config.dji_mic_3.volume_name_patterns == ("DJI*", "MIC*")
    assert config.dji_mic_3.audio_globs == ("**/*.WAV", "**/*.wav")
    assert config.dji_mic_3.stable_seconds == 10


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


def test_app_config_defaults_match_mock_first_slice() -> None:
    config = AppConfig()

    assert config.vad_backend == "mock"
    assert config.asr_backend == "mock"
    # The LLM default is rule_based so the pipeline is safe to run without an API key.
    assert config.llm_backend == "rule_based"
    assert "NO NAME" in config.dji_mic_3.volume_name_patterns


def test_default_max_chunk_ms_is_bounded_for_production_audio() -> None:
    config = AppConfig()

    assert config.max_chunk_ms == 120_000


def test_app_config_defaults_are_production_safe_without_llm_key() -> None:
    config = AppConfig()

    assert config.vad_backend == "mock"
    assert config.asr_backend == "mock"
    assert config.llm_backend == "rule_based"
    assert config.llm_command is None
    assert "NO NAME" in config.dji_mic_3.volume_name_patterns


def test_app_config_resolves_obsidian_and_archive_paths_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
[paths]
data_dir = "pcn-data"
obsidian_vault = "vault"
nas_archive_root = "~/pcn-nas"
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.obsidian_vault == tmp_path / "config" / "vault"
    assert config.nas_archive_root == (Path.home() / "pcn-nas").resolve(strict=False)


def test_app_config_loads_command_timeout_seconds(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        """
[commands]
timeout_seconds = 12
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.command_timeout_seconds == 12


def test_app_config_rejects_non_positive_command_timeout_seconds(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        AppConfig(command_timeout_seconds=0)

    config_path = tmp_path / "local.toml"
    config_path.write_text("[commands]\ntimeout_seconds = -5\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        AppConfig.from_toml(config_path)


def test_app_config_has_default_asr_device_mps() -> None:
    assert AppConfig().asr_device == "mps"


def test_app_config_default_asr_mode_is_chunk() -> None:
    # The whole-file diarize path is opt-in; the default must stay the per-chunk SenseVoice path.
    config = AppConfig()
    assert config.asr_mode == "chunk"
    assert config.asr_diarize_model == "paraformer-zh"
    assert config.asr_punc_model == "ct-punc"
    assert config.asr_spk_model == "cam++"
    assert config.asr_spk_mode == "punc_segment"
    assert config.asr_preset_spk_num is None


def test_app_config_loads_asr_diarize_block(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        """
[asr]
backend = "funasr_server"
mode = "diarize"
diarize_model = "paraformer-en"
punc_model = "ct-punc-en"
spk_model = "eres2net"
spk_mode = "punc_segment"
preset_spk_num = 3
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.asr_mode == "diarize"
    assert config.asr_diarize_model == "paraformer-en"
    assert config.asr_punc_model == "ct-punc-en"
    assert config.asr_spk_model == "eres2net"
    assert config.asr_spk_mode == "punc_segment"
    assert config.asr_preset_spk_num == 3
