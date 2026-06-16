#!/usr/bin/env python3
"""Resident CAM++ speaker-embedding wrapper.

Loads a CAM++ speaker model ONCE and then, in ``--server`` mode, reads work items
line-by-line on stdin -- one JSON object ``{"segment_id": str, "audio_path": str}`` per
line -- and emits one JSON line per item carrying the 192-dim voiceprint:
``{"segment_id": ..., "embedding": [..192 floats..]}``. A per-item failure rides the
stream as ``{"segment_id": ..., "error": "<msg>"}`` and does NOT kill the daemon.

Mirrors ``funasr_paraformer_diarize_wrapper.py``: argparse, the resident stdin loop,
MPS-fallback env, ``disable_update=True``, JSON-per-line I/O, and a model import kept
INSIDE the server function so unit tests need neither funasr nor torch.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # unsupported MPS ops fall back to CPU


def resolve_device(requested: str, *, mps_available=None) -> str:
    if requested != "mps":
        return requested
    if mps_available is None:
        import torch
        mps_available = torch.backends.mps.is_available
    return "mps" if mps_available() else "cpu"


def normalize_embedding(spk_embedding) -> list[float]:
    """Flatten a CAM++ ``spk_embedding`` to a 1-D Python list of floats (192 for real CAM++).

    Accepts a tensor-like exposing ``.detach().cpu().numpy()``, a numpy array, or a nested
    list. Done generically so this module imports without torch/funasr.
    """
    import numpy as np  # numpy is a runtime dependency; torch/funasr are NOT imported here

    try:
        array = spk_embedding.detach().cpu().numpy()
    except AttributeError:
        array = np.asarray(spk_embedding)
    return [float(v) for v in np.asarray(array).ravel().tolist()]


def run_server(model, in_stream, out_stream) -> int:
    """Resident loop: one ``{"segment_id", "audio_path"}`` JSON per input line -> one
    ``{"segment_id", "embedding"}`` (or ``{"segment_id", "error"}``) JSON per output line."""
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
                result = model.generate(input=audio_path)
            embedding = normalize_embedding(result[0]["spk_embedding"])
            payload = {"segment_id": segment_id, "embedding": embedding}
        except Exception as exc:  # one bad item must not kill the resident server
            payload = {"segment_id": segment_id, "error": f"{type(exc).__name__}: {exc}"}
        out_stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out_stream.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resident CAM++ speaker-embedding wrapper: emit a 192-dim voiceprint per segment."
    )
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--spk-model", default="cam++")
    parser.add_argument("--device", default="cpu")
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
                    model=args.spk_model,
                    device=resolve_device(args.device),
                    disable_update=True,
                )
        except Exception as exc:  # model download / device (MPS OOM) failure is environmental
            print(f"FunASR model load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return run_server(model, sys.stdin, sys.stdout)

    parser.error("this wrapper only runs in --server mode")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
