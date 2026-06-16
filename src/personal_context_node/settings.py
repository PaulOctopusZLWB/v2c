"""DB-backed runtime settings store.

A small allow-listed key/value store (the `settings` table) that lets the web change
ASR mode + LLM model etc. without a restart. The worker re-reads overrides at the top of
every drain (see web/worker.py), so changes take effect on the NEXT drain:
  - ASR overrides are applied via AppConfig.model_copy(update=...).
  - GLM_* overrides are exported to os.environ before the drain; the glm_llm_wrapper
    subprocess inherits them.

Three entry points:
  - read_overrides(config): the validated, allow-listed, typed override dict (for the worker).
  - write_settings(config, updates): validate + persist (or delete on None).
  - effective_settings(config): override > env > config/default, typed (for the GET form).
"""
from __future__ import annotations

import os
from typing import Any

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, get_settings, initialize, put_setting

# The wrapper's default GLM endpoint (scripts/glm_llm_wrapper.DEFAULT_BASE_URL). Duplicated here
# rather than imported because that script is not an importable package module.
_DEFAULT_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_DEFAULT_GLM_MODEL = "glm-4-flash"
_TRUTHY = {"1", "true", "enabled"}

ALLOW_LIST = {"asr_mode", "asr_preset_spk_num", "glm_model", "glm_base_url", "glm_thinking"}
_ASR_MODES = {"chunk", "diarize"}

# setting key -> wrapper env var. These reach the glm_llm_wrapper subprocess via os.environ.
_GLM_ENV = {"glm_model": "GLM_MODEL", "glm_base_url": "GLM_BASE_URL", "glm_thinking": "GLM_THINKING"}
# Snapshot the GLM env as it was at PROCESS LAUNCH (before any drain mutates it). A cleared web
# override must revert to THIS, not to the last-applied override value — and effective_settings
# reads this (not the live, worker-mutated os.environ) so GET never reports a stale override.
_LAUNCH_GLM_ENV = {env: os.environ.get(env) for env in _GLM_ENV.values()}


def apply_glm_env(overrides: dict[str, Any]) -> None:
    """Set os.environ for the GLM_* wrapper vars from override -> launch baseline -> unset. Called
    at the top of every drain so a setting change (or its removal) takes effect on the next run."""
    for setting_key, env_name in _GLM_ENV.items():
        if setting_key in overrides:
            value = overrides[setting_key]
            os.environ[env_name] = ("true" if value else "false") if setting_key == "glm_thinking" else str(value)
        else:
            baseline = _LAUNCH_GLM_ENV.get(env_name)
            if baseline is None:
                os.environ.pop(env_name, None)  # not set at launch -> unset so the wrapper default applies
            else:
                os.environ[env_name] = baseline


def _parse_bool(value: object) -> bool:
    return str(value).strip().lower() in _TRUTHY


def _coerce_for_storage(key: str, value: Any) -> str | None:
    """Validate one allow-listed key and return its string form for storage, or None to delete.

    Raises ValueError on an unknown key or an invalid value (so write_settings is strict).
    """
    if key not in ALLOW_LIST:
        raise ValueError(f"unknown setting: {key}")
    if value is None:
        return None
    if key == "asr_mode":
        if value not in _ASR_MODES:
            raise ValueError(f"asr_mode must be one of {sorted(_ASR_MODES)}, got {value!r}")
        return str(value)
    if key == "asr_preset_spk_num":
        try:
            num = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"asr_preset_spk_num must be an integer, got {value!r}") from exc
        if num <= 0:
            raise ValueError(f"asr_preset_spk_num must be positive, got {num}")
        return str(num)
    if key == "glm_thinking":
        return "true" if (value is True or _parse_bool(value)) else "false"
    # glm_model / glm_base_url
    return str(value)


def effective_config(config: AppConfig) -> AppConfig:
    """config with the ASR overrides (asr_mode/asr_preset_spk_num) applied. Used wherever config
    drives behavior at RUNTIME — both the adapter build AND import-time task routing (ingest.py
    chooses vad vs transcribe_diarize from config.asr_mode), so the diarize toggle takes effect on
    the next import as well as the next drain."""
    overrides = read_overrides(config)
    asr = {k: v for k, v in overrides.items() if k in ("asr_mode", "asr_preset_spk_num")}
    return config.model_copy(update=asr) if asr else config


def read_overrides(config: AppConfig) -> dict[str, Any]:
    """Open a connection, read settings, return ONLY allow-listed keys, parsed & validated.

    Invalid stored values are silently skipped (the worker should never crash on a bad row).
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        raw = get_settings(conn)
    finally:
        conn.close()

    overrides: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in ALLOW_LIST:
            continue
        if key == "asr_mode":
            if value in _ASR_MODES:
                overrides["asr_mode"] = value
        elif key == "asr_preset_spk_num":
            try:
                num = int(value)
            except (TypeError, ValueError):
                continue
            if num > 0:
                overrides["asr_preset_spk_num"] = num
        elif key == "glm_thinking":
            overrides["glm_thinking"] = _parse_bool(value)
        else:  # glm_model / glm_base_url
            overrides[key] = str(value)
    return overrides


def write_settings(config: AppConfig, updates: dict[str, Any]) -> None:
    """Validate against ALLOW_LIST + per-key rules, then persist each (None deletes the row)."""
    # Validate everything before writing anything (an unknown/invalid key aborts the whole write).
    coerced: dict[str, str | None] = {key: _coerce_for_storage(key, value) for key, value in updates.items()}
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for key, stored in coerced.items():
            if stored is None:
                conn.execute("delete from settings where key = ?", (key,))
            else:
                put_setting(conn, key, stored)
        conn.commit()
    finally:
        conn.close()


def effective_settings(config: AppConfig) -> dict[str, Any]:
    """Current effective values for the GET form: override > env > config/default.

    Returns typed values (asr_preset_spk_num int|None, glm_thinking bool).
    """
    overrides = read_overrides(config)

    asr_mode = overrides.get("asr_mode", config.asr_mode)
    asr_preset_spk_num = overrides.get("asr_preset_spk_num", config.asr_preset_spk_num)

    # GLM fallbacks use the LAUNCH baseline, NOT the live os.environ (which the worker mutates each
    # drain) — otherwise a cleared override would report its own stale, last-applied value.
    if "glm_model" in overrides:
        glm_model = overrides["glm_model"]
    else:
        glm_model = _LAUNCH_GLM_ENV.get("GLM_MODEL") or _DEFAULT_GLM_MODEL

    if "glm_base_url" in overrides:
        glm_base_url = overrides["glm_base_url"]
    else:
        glm_base_url = _LAUNCH_GLM_ENV.get("GLM_BASE_URL") or _DEFAULT_GLM_BASE_URL

    if "glm_thinking" in overrides:
        glm_thinking = overrides["glm_thinking"]
    else:
        baseline = _LAUNCH_GLM_ENV.get("GLM_THINKING")
        glm_thinking = _parse_bool(baseline) if baseline is not None else False

    return {
        "asr_mode": asr_mode,
        "asr_preset_spk_num": asr_preset_spk_num,
        "glm_model": glm_model,
        "glm_base_url": glm_base_url,
        "glm_thinking": glm_thinking,
    }
