"""Pipeline-facing feature extraction: CAM++ voiceprints + emotion2vec per audio file.

The ``extract_features`` task is a pure LEAF of the pipeline DAG — it fans in from
``transcribe_diarize``/``asr`` but has no downstream edge, so a slow or failed extraction can
never delay sessions, summaries, or reports. Idempotency comes for free from the pending-scope
queries (a re-run finds fewer or zero pending segments and no-ops), so standard task retry
semantics apply with no bespoke logic here.
"""
from __future__ import annotations

import contextlib

from personal_context_node.config import AppConfig


def build_extraction_adapters(*, config: AppConfig) -> tuple[object, object]:
    """Build the resident CAM++ embed + emotion2vec adapters from config.

    Mirrors the web worker's default factories (same wrapper commands, same device/precision
    flags) so CLI drains and the pipeline task produce byte-identical artifacts to the manual
    extraction routes. A mock ASR backend gets mock extraction adapters too — the whole model
    stack is stubbed together, keeping run-all/E2E flows hermetic (no funasr subprocess).
    """
    if config.asr_backend == "mock":
        from personal_context_node.adapters.embed.mock import MockEmbedAdapter
        from personal_context_node.adapters.emotion.mock import MockEmotionAdapter

        return (MockEmbedAdapter(), MockEmotionAdapter())

    from personal_context_node.adapters.embed.command import PersistentCommandEmbedAdapter
    from personal_context_node.adapters.emotion.command import PersistentCommandEmotionAdapter

    embed_command = [
        "python3", "scripts/funasr_campplus_embed_wrapper.py", "--server",
        "--device", config.asr_device,
    ]
    emotion_command = [
        "python3", "scripts/funasr_emotion2vec_wrapper.py", "--server",
        "--device", config.asr_device,
    ]
    if config.asr_precision == "fp16":
        embed_command.extend(["--precision", "fp16"])
        emotion_command.extend(["--precision", "fp16"])
    return (
        PersistentCommandEmbedAdapter(command=embed_command),
        PersistentCommandEmotionAdapter(command=emotion_command),
    )


def close_extraction_adapters(adapters: tuple[object, object] | None) -> None:
    """Best-effort close of a (embed, emotion) adapter pair; never raises."""
    if adapters is None:
        return
    for adapter in adapters:
        with contextlib.suppress(Exception):
            adapter.close()


def extract_features_for_audio_file(
    *,
    config: AppConfig,
    audio_file_id: str,
    embed: object | None = None,
    emotion: object | None = None,
) -> dict:
    """Run the combined (batched, concurrent) embedding + emotion pass over ONE audio file.

    ``embed``/``emotion`` are the resident adapters (objects exposing ``embed``/``embed_batch``
    and ``classify``/``classify_batch``); when either is omitted a fresh pair is built from
    config and closed afterwards, so a bare CLI drain works without a resident-adapter host.
    """
    from personal_context_node.speaker_embeddings import extract_pending_embeddings_and_emotions

    owned: tuple[object, object] | None = None
    if embed is None or emotion is None:
        owned = build_extraction_adapters(config=config)
        embed, emotion = owned
    try:
        result = extract_pending_embeddings_and_emotions(
            config=config,
            embed_fn=embed.embed,
            classify_fn=emotion.classify,
            embed_batch_fn=getattr(embed, "embed_batch", None),
            classify_batch_fn=getattr(emotion, "classify_batch", None),
            audio_file_id=audio_file_id,
            batch_size=max(1, int(getattr(config, "extraction_batch_size", 32) or 32)),
        )
    finally:
        close_extraction_adapters(owned)
    # A pass where EVERYTHING attempted failed means the extractor itself is broken (dead
    # wrapper, missing model) — fail the task loudly so queue retry/backoff and the metrics
    # panel surface it. Partial failures (a corrupt segment among successes) stay a success:
    # retrying cannot fix a bad wav, and the failed segment simply stays pending.
    embedding = result.get("embedding", {})
    emotion_result = result.get("emotion", {})
    embed_all_failed = embedding.get("failed", 0) > 0 and embedding.get("embedded", 0) == 0
    emotion_all_failed = emotion_result.get("failed", 0) > 0 and emotion_result.get("emoted", 0) == 0
    if embed_all_failed or emotion_all_failed:
        raise RuntimeError(f"feature extraction failed wholesale for {audio_file_id}: {result}")
    return result
