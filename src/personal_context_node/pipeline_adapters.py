from __future__ import annotations

import shlex
from dataclasses import dataclass

from personal_context_node.adapters.asr.command import CommandASRAdapter
from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.llm.command import CommandLLMAdapter
from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.adapters.vad.command import CommandVADAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRPort
from personal_context_node.core.ports.llm import LLMPort
from personal_context_node.core.ports.vad import VADPort


@dataclass(frozen=True)
class PipelineAdapters:
    vad: VADPort
    asr: ASRPort
    llm: LLMPort


def build_vad(
    *,
    vad_backend: str,
    vad_command: str | None,
    vad_threshold: float,
    merge_gap_ms: int = 250,
    min_speech_ms: int = 300,
    model_id: str = "fsmn-vad",
    model_revision: str | None = None,
) -> VADPort:
    if vad_backend == "energy":
        return EnergyVadAdapter(threshold=vad_threshold, merge_gap_ms=merge_gap_ms, min_speech_ms=min_speech_ms)
    if vad_backend == "mock":
        return MockVADAdapter()
    if vad_backend == "command":
        if not vad_command:
            raise ValueError("vad_command is required when vad_backend is 'command'")
        return CommandVADAdapter(command=shlex.split(vad_command), merge_gap_ms=merge_gap_ms, min_speech_ms=min_speech_ms)
    if vad_backend == "funasr":
        if vad_command:
            command = shlex.split(vad_command)
        else:
            # Pass the configurable VAD threshold to the wrapper; merge_gap/min_speech are
            # applied adapter-side (§5 "VAD 阈值必须配置化").
            command = [
                "python3",
                "scripts/funasr_vad_wrapper.py",
                "--model",
                model_id,
                "--threshold",
                str(vad_threshold),
            ]
            if model_revision is not None:
                command.extend(["--model-revision", model_revision])
        return CommandVADAdapter(command=command, merge_gap_ms=merge_gap_ms, min_speech_ms=min_speech_ms)
    raise ValueError("vad_backend must be 'energy', 'mock', 'command', or 'funasr'")


def build_asr(
    *,
    asr_backend: str,
    asr_command: str | None,
    mock_text: str | None,
    language: str = "zh",
    model_name: str = "mock-asr",
    model_id: str = "iic/SenseVoiceSmall",
    model_version: str = "funasr-sensevoice-local",
) -> ASRPort:
    if asr_backend == "mock":
        return MockASRAdapter(text=mock_text, language=language, model_name=model_name)
    if asr_backend == "command":
        if not asr_command:
            raise ValueError("asr_command is required when asr_backend is 'command'")
        return CommandASRAdapter(command=shlex.split(asr_command))
    if asr_backend == "funasr":
        command = (
            shlex.split(asr_command)
            if asr_command
            else [
                "python3",
                "scripts/funasr_sensevoice_wrapper.py",
                "--model",
                model_id,
                "--model-version",
                model_version,
                "--language",
                language,
            ]
        )
        return CommandASRAdapter(command=command)
    raise ValueError("asr_backend must be 'mock', 'command', or 'funasr'")


def build_llm(*, llm_backend: str, llm_command: str | None) -> LLMPort:
    if llm_backend == "rule_based":
        return RuleBasedLLMAdapter()
    if llm_backend == "mock":
        return MockLLMAdapter()
    if llm_backend == "command":
        if not llm_command:
            raise ValueError("llm_command is required when llm_backend is 'command'")
        return CommandLLMAdapter(command=shlex.split(llm_command))
    raise ValueError("llm_backend must be 'rule_based', 'mock', or 'command'")


def build_pipeline_adapters(*, config: AppConfig) -> PipelineAdapters:
    return PipelineAdapters(
        vad=build_vad(
            vad_backend=config.vad_backend,
            vad_command=config.vad_command,
            vad_threshold=config.vad_threshold,
            merge_gap_ms=config.merge_gap_ms,
            min_speech_ms=config.min_speech_ms,
            model_id=config.vad_model_id,
            model_revision=config.vad_model_revision,
        ),
        asr=build_asr(
            asr_backend=config.asr_backend,
            asr_command=config.asr_command,
            mock_text=None,
            language=config.asr_language,
            model_name=config.asr_model_name,
            model_id=config.asr_model_id,
            model_version=config.asr_model_version,
        ),
        llm=build_llm(llm_backend=config.llm_backend, llm_command=config.llm_command),
    )
