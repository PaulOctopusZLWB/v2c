from __future__ import annotations

from pathlib import Path

import pytest

from personal_context_node import settings as _settings
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, get_settings, initialize, put_setting


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")


def test_put_setting_get_settings_round_trip(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        put_setting(conn, "asr_mode", "diarize")
        put_setting(conn, "glm_model", "glm-5.1")
        conn.commit()
        rows = get_settings(conn)
    finally:
        conn.close()
    assert rows["asr_mode"] == "diarize"
    assert rows["glm_model"] == "glm-5.1"


def test_put_setting_upserts_and_sets_updated_at(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        put_setting(conn, "asr_mode", "chunk")
        put_setting(conn, "asr_mode", "diarize")
        conn.commit()
        row = conn.execute("select value, updated_at from settings where key = 'asr_mode'").fetchone()
    finally:
        conn.close()
    assert row[0] == "diarize"  # upsert replaced value, not a second row
    assert row[1]  # non-empty updated_at stamped on upsert


def test_read_overrides_only_allow_listed_keys(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        put_setting(conn, "asr_mode", "diarize")
        put_setting(conn, "not_allowed", "boom")
        conn.commit()
    finally:
        conn.close()
    overrides = _settings.read_overrides(config)
    assert overrides == {"asr_mode": "diarize"}
    assert "not_allowed" not in overrides


def test_read_overrides_rejects_bad_asr_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        put_setting(conn, "asr_mode", "garbage")
        conn.commit()
    finally:
        conn.close()
    assert "asr_mode" not in _settings.read_overrides(config)


def test_read_overrides_parses_preset_spk_num_int(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        put_setting(conn, "asr_preset_spk_num", "3")
        put_setting(conn, "glm_thinking", "true")
        conn.commit()
    finally:
        conn.close()
    overrides = _settings.read_overrides(config)
    assert overrides["asr_preset_spk_num"] == 3
    assert isinstance(overrides["asr_preset_spk_num"], int)
    assert overrides["glm_thinking"] is True


def test_read_overrides_skips_invalid_preset_spk_num(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        put_setting(conn, "asr_preset_spk_num", "0")
        conn.commit()
    finally:
        conn.close()
    assert "asr_preset_spk_num" not in _settings.read_overrides(config)


def test_write_settings_persists_valid_values(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _settings.write_settings(
        config,
        {
            "asr_mode": "diarize",
            "asr_preset_spk_num": 2,
            "glm_model": "glm-5.1",
            "glm_thinking": True,
        },
    )
    overrides = _settings.read_overrides(config)
    assert overrides["asr_mode"] == "diarize"
    assert overrides["asr_preset_spk_num"] == 2
    assert overrides["glm_model"] == "glm-5.1"
    assert overrides["glm_thinking"] is True


def test_write_settings_raises_on_unknown_key(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError):
        _settings.write_settings(config, {"bogus": "x"})


def test_write_settings_raises_on_invalid_asr_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(ValueError):
        _settings.write_settings(config, {"asr_mode": "nope"})


def test_write_settings_none_deletes_row(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _settings.write_settings(config, {"glm_model": "glm-5.1"})
    assert _settings.read_overrides(config)["glm_model"] == "glm-5.1"
    _settings.write_settings(config, {"glm_model": None})
    assert "glm_model" not in _settings.read_overrides(config)


def test_effective_settings_default_when_no_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GLM_MODEL", raising=False)
    monkeypatch.delenv("GLM_BASE_URL", raising=False)
    monkeypatch.delenv("GLM_THINKING", raising=False)
    config = _config(tmp_path)
    eff = _settings.effective_settings(config)
    assert eff["asr_mode"] == config.asr_mode
    assert eff["asr_preset_spk_num"] == config.asr_preset_spk_num
    assert eff["glm_model"] == "glm-4-flash"
    assert eff["glm_base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert eff["glm_thinking"] is False


def test_effective_settings_env_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GLM_MODEL", "glm-from-env")
    config = _config(tmp_path)
    eff = _settings.effective_settings(config)
    assert eff["glm_model"] == "glm-from-env"


def test_effective_settings_override_beats_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GLM_MODEL", "glm-from-env")
    config = _config(tmp_path)
    _settings.write_settings(config, {"glm_model": "glm-from-db"})
    eff = _settings.effective_settings(config)
    assert eff["glm_model"] == "glm-from-db"


def test_effective_settings_returns_typed_values(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _settings.write_settings(config, {"asr_preset_spk_num": 4, "glm_thinking": True})
    eff = _settings.effective_settings(config)
    assert eff["asr_preset_spk_num"] == 4
    assert isinstance(eff["asr_preset_spk_num"], int)
    assert eff["glm_thinking"] is True


def test_worker_drain_applies_overrides_to_adapter_config(tmp_path: Path, monkeypatch) -> None:
    # A stored asr_mode/preset override must reach build_pipeline_adapters as an effective config,
    # and a glm_model override must be exported to os.environ before the drain — both on the NEXT
    # drain, no restart. Empty queue so the drain returns fast.
    import os

    import personal_context_node.web.worker as _worker_module
    from personal_context_node.pipeline_adapters import PipelineAdapters
    from personal_context_node.web.worker import PipelineWorker

    monkeypatch.delenv("GLM_MODEL", raising=False)
    config = _config(tmp_path)
    _settings.write_settings(
        config,
        {"asr_mode": "diarize", "asr_preset_spk_num": 2, "glm_model": "glm-5.1"},
    )

    captured: dict[str, object] = {}

    def fake_build(*, config):  # noqa: A002 - mirror real signature
        captured["config"] = config
        return PipelineAdapters(vad=object(), asr=object(), llm=object())

    monkeypatch.setattr(_worker_module, "build_pipeline_adapters", fake_build)

    worker = PipelineWorker(config=config)
    result = worker.drain_now()  # empty queue -> returns immediately

    assert result.status == "complete"
    effective = captured["config"]
    assert effective.asr_mode == "diarize"
    assert effective.asr_preset_spk_num == 2
    assert os.environ["GLM_MODEL"] == "glm-5.1"
