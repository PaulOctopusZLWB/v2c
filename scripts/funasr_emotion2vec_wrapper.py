#!/usr/bin/env python3
"""Resident emotion2vec acoustic-emotion wrapper.

Loads an emotion2vec model ONCE and then, in ``--server`` mode, reads work items
line-by-line on stdin -- one JSON object ``{"segment_id": str, "audio_path": str}`` per
line -- and emits one JSON line per item carrying the 8-class acoustic emotion:
``{"segment_id": ..., "label": "<dominant>", "scores": {label: float, ...}}``. A per-item
failure rides the stream as ``{"segment_id": ..., "error": "<msg>"}`` and does NOT kill
the daemon.

Mirrors ``funasr_campplus_embed_wrapper.py``: argparse, the resident stdin loop,
MPS-fallback env, ``disable_update=True``, JSON-per-line I/O, and a model import kept
INSIDE the server function so unit tests need neither funasr nor torch.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # unsupported MPS ops fall back to CPU

logger = logging.getLogger(__name__)


def resolve_device(requested: str, *, mps_available=None) -> str:
    if requested != "mps":
        return requested
    if mps_available is None:
        import torch
        mps_available = torch.backends.mps.is_available
    return "mps" if mps_available() else "cpu"


def maybe_half(model, precision: str):
    """Best-effort cast a loaded FunASR model to fp16 in place; return it either way.

    ``precision == "fp32"`` (the default) is a no-op. ``"fp16"`` calls ``model.half()`` on every
    reachable inner torch.nn.Module. FunASR's AutoModel does not accept a dtype kwarg, so this is
    done via torch AFTER load rather than at construction. Some ops are unsupported in fp16 on a
    given backend (notably MPS) -- any failure here is caught, logged as a warning, and the model
    is left in fp32 so the wrapper never crashes just because fp16 was requested.
    """
    if precision != "fp16":
        return model
    try:
        _cast_model_half(model)
    except Exception as exc:  # pragma: no cover - defensive: never let precision break inference
        logger.warning("fp16 conversion failed (%s: %s); falling back to fp32", type(exc).__name__, exc)
    return model


def _cast_model_half(model) -> None:
    """Walk common FunASR AutoModel attribute names and .half() any torch.nn.Module found."""
    import torch.nn as nn

    candidates = [model]
    for attr in ("model", "punc_model", "vad_model", "spk_model"):
        inner = getattr(model, attr, None)
        if inner is not None:
            candidates.append(inner)
    for candidate in candidates:
        if isinstance(candidate, nn.Module):
            candidate.half()


def normalize_emotion(labels, scores) -> tuple[str, dict[str, float]]:
    """Pair emotion2vec ``labels`` with ``scores`` and pick the dominant class.

    Returns ``(dominant_label, {label: float_score, ...})``. Scores are coerced to plain
    Python floats (the model returns numpy floats). Done generically so this module imports
    without torch/funasr.
    """
    scores_dict = {label: float(score) for label, score in zip(labels, scores)}
    dominant = max(scores_dict, key=scores_dict.get)
    return dominant, scores_dict


def run_server(model, in_stream, out_stream) -> int:
    """Resident loop: one ``{"segment_id", "audio_path"}`` JSON per input line -> one
    ``{"segment_id", "label", "scores"}`` (or ``{"segment_id", "error"}``) JSON per output line."""
    for raw_line in in_stream:
        line = raw_line.strip()
        if not line:
            continue
        segment_id = None
        try:
            item = json.loads(line)
            segment_id = item.get("segment_id")
            audio_path = item["audio_path"]
            # FunASR/tqdm may print to stdout during inference; redirect it to stderr so only
            # our explicit JSON line reaches the one-line-per-result protocol stream.
            with contextlib.redirect_stdout(sys.stderr):
                result = model.generate(audio_path, granularity="utterance", extract_embedding=False)
            record = result[0]
            label, scores = normalize_emotion(record["labels"], record["scores"])
            payload = {"segment_id": segment_id, "label": label, "scores": scores}
        except Exception as exc:  # one bad item must not kill the resident server
            payload = {"segment_id": segment_id, "error": f"{type(exc).__name__}: {exc}"}
        out_stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out_stream.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resident emotion2vec wrapper: emit an 8-class acoustic emotion per segment."
    )
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--model", default="iic/emotion2vec_plus_base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp32")
    args = parser.parse_args()

    if args.server:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                from funasr import AutoModel
        except ImportError:
            print(
                "FunASR is not installed. Install it in the uv/Docker runtime that runs this wrapper.",
                file=sys.stderr,
            )
            return 2
        try:
            with contextlib.redirect_stdout(sys.stderr):
                model = AutoModel(
                    model=args.model,
                    device=resolve_device(args.device),
                    disable_update=True,
                )
                model = maybe_half(model, args.precision)
        except Exception as exc:  # model download / device (MPS OOM) failure is environmental
            print(f"FunASR model load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return run_server(model, sys.stdin, sys.stdout)

    parser.error("this wrapper only runs in --server mode")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
