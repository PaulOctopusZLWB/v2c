#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # unsupported MPS ops fall back to CPU


def resolve_device(requested: str, *, mps_available=None) -> str:
    if requested != "mps":
        return requested
    if mps_available is None:
        import torch
        mps_available = torch.backends.mps.is_available
    return "mps" if mps_available() else "cpu"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FunASR SenseVoice and emit Personal Context Node ASR JSON.")
    parser.add_argument("audio_path", type=Path, nargs="?", default=None)
    parser.add_argument("--model", default="iic/SenseVoiceSmall")
    parser.add_argument("--vad-model", default=None)
    parser.add_argument("--model-version", default="funasr-sensevoice-local")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--batch-size-s", type=int, default=300)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()

    if args.server:
        import contextlib as _ctx
        with _ctx.redirect_stdout(sys.stderr):
            from funasr import AutoModel
            model = AutoModel(model=args.model, device=resolve_device(args.device))
        return run_server(model, sys.stdin, sys.stdout, language=args.language)

    # Exit-code contract (mirrors CommandASRAdapter): 3 = permanently unsupported
    # input (terminal); 2 = transient/environment failure (retryable).
    terminal_exit_code = 3
    retryable_exit_code = 2

    if args.audio_path is None or not args.audio_path.exists():
        print(f"audio file does not exist: {args.audio_path}", file=sys.stderr)
        return terminal_exit_code

    try:
        with contextlib.redirect_stdout(sys.stderr):
            from funasr import AutoModel
    except ImportError:
        print(
            "FunASR is not installed. Install it in the uv/Docker runtime that runs this wrapper.",
            file=sys.stderr,
        )
        return retryable_exit_code

    model_kwargs: dict[str, Any] = {"model": args.model, "device": resolve_device(args.device)}
    if args.vad_model:
        model_kwargs["vad_model"] = args.vad_model
    with contextlib.redirect_stdout(sys.stderr):
        model = AutoModel(**model_kwargs)
        raw_result = model.generate(
            input=str(args.audio_path),
            language=args.language,
            use_itn=True,
            batch_size_s=args.batch_size_s,
        )
    payload = {
        "model_name": "sensevoice",
        "model_version": args.model_version,
        "segments": _normalize_segments(raw_result),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _normalize_segments(raw_result: Any) -> list[dict[str, object]]:
    records = raw_result if isinstance(raw_result, list) else [raw_result]
    segments: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        sentence_info = record.get("sentence_info")
        if isinstance(sentence_info, list) and sentence_info:
            for sentence in sentence_info:
                if isinstance(sentence, dict):
                    segments.append(_normalize_sentence(sentence, fallback_text=str(record.get("text", ""))))
        else:
            text, tags = _split_text_tags(record.get("text", ""))
            if text:
                segments.append(
                    {
                        "text": text,
                        "tags": tags,
                        "start_ms": int(record.get("start", 0) or 0),
                        "end_ms": int(record.get("end", record.get("duration", 0)) or 0),
                        "confidence": _confidence(record),
                        "language": str(record.get("language", "zh") or "zh"),
                        "speaker": str(record.get("speaker", record.get("spk", "unknown")) or "unknown"),
                    }
                )
    return segments


def _normalize_sentence(sentence: dict[str, Any], *, fallback_text: str) -> dict[str, object]:
    start_ms, end_ms = _time_bounds(sentence)
    text, tags = _split_text_tags(sentence.get("text", fallback_text))
    return {
        "text": text,
        "tags": tags,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": _confidence(sentence),
        "language": str(sentence.get("language", "zh") or "zh"),
        "speaker": str(sentence.get("speaker", sentence.get("spk", "unknown")) or "unknown"),
    }


def _time_bounds(record: dict[str, Any]) -> tuple[int, int]:
    if "timestamp" in record and isinstance(record["timestamp"], list) and len(record["timestamp"]) >= 2:
        return int(record["timestamp"][0]), int(record["timestamp"][1])
    return int(record.get("start", 0) or 0), int(record.get("end", 0) or 0)


def _confidence(record: dict[str, Any]) -> float | None:
    value = record.get("confidence", record.get("score"))
    if value is None:
        return None
    return float(value)


def _split_text_tags(value: object) -> tuple[str, list[str]]:
    text = str(value)
    tags = re.findall(r"<\|([^|>]+)\|>", text)
    return re.sub(r"<\|[^|>]+\|>", "", text).strip(), tags


def run_server(model, stdin, stdout, *, language: str) -> int:
    """Resident loop: one chunk path per input line -> one result JSON per output line."""
    for raw_line in stdin:
        path = raw_line.strip()
        if not path:
            continue
        try:
            result = model.generate(input=path, language=language, use_itn=True, batch_size_s=300)
            payload = {"model_name": "sensevoice", "model_version": "funasr-sensevoice-server",
                       "segments": _normalize_segments(result)}
        except Exception as exc:  # one bad chunk must not kill the resident server
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
