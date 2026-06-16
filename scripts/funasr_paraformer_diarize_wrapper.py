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
    parser = argparse.ArgumentParser(
        description="Run FunASR Paraformer whole-file diarization and emit Personal Context Node ASR JSON."
    )
    parser.add_argument("audio_path", type=Path, nargs="?", default=None)
    parser.add_argument("--model", default="paraformer-zh")
    parser.add_argument("--vad-model", default="fsmn-vad")
    parser.add_argument("--punc-model", default="ct-punc")
    parser.add_argument("--spk-model", default="cam++")
    parser.add_argument("--spk-mode", default="punc_segment")
    parser.add_argument("--model-version", default="funasr-paraformer-diarize-local")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--batch-size-s", type=int, default=300)
    # Force the CAM++ clusterer to a known speaker count (mitigates auto-clustering's tendency to
    # over-segment one speaker into several). Omitted -> FunASR auto-determines the count.
    parser.add_argument("--preset-spk-num", type=int, default=None)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--server", action="store_true")
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
                    vad_model=args.vad_model,
                    punc_model=args.punc_model,
                    spk_model=args.spk_model,
                    spk_mode=args.spk_mode,
                    device=resolve_device(args.device),
                    disable_update=True,
                )
        except Exception as exc:  # model download / device (MPS OOM) failure is environmental
            print(f"FunASR model load failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return run_server(
            model, sys.stdin, sys.stdout,
            language=args.language, batch_size_s=args.batch_size_s, model_version=args.model_version,
            preset_spk_num=args.preset_spk_num,
        )

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

    with contextlib.redirect_stdout(sys.stderr):
        model = AutoModel(
            model=args.model,
            vad_model=args.vad_model,
            punc_model=args.punc_model,
            spk_model=args.spk_model,
            spk_mode=args.spk_mode,
            device=resolve_device(args.device),
            disable_update=True,
        )
        gen_kwargs = {"batch_size_s": args.batch_size_s}
        if args.preset_spk_num is not None:
            gen_kwargs["preset_spk_num"] = args.preset_spk_num
        raw_result = model.generate(input=str(args.audio_path), **gen_kwargs)
    payload = {
        "model_name": "paraformer-diarize",
        "model_version": args.model_version,
        "segments": normalize_diarized(_sentence_info(raw_result), language=args.language),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _sentence_info(raw_result: Any) -> list:
    """Pull the sentence_info list out of FunASR's result[0]; tolerate odd shapes."""
    records = raw_result if isinstance(raw_result, list) else [raw_result]
    for record in records:
        if isinstance(record, dict):
            sentence_info = record.get("sentence_info")
            if isinstance(sentence_info, list):
                return sentence_info
    return []


def normalize_diarized(sentence_info: list, *, language: str = "zh") -> list[dict[str, object]]:
    """Map FunASR sentence_info dicts to PCN segment dicts with first-appearance speaker labels.

    Speaker mapping: the integer ``spk`` cluster index is mapped to ``spk_NN`` (zero-padded
    width 2, 1-based) ordered by FIRST APPEARANCE in the list -- NOT by the raw int value.
    If exactly one distinct speaker is present (or ``spk`` is missing/None on all sentences),
    every segment collapses to ``"self"`` to preserve the default-self prior.
    """
    if not isinstance(sentence_info, list):
        return []

    sentences = [s for s in sentence_info if isinstance(s, dict)]

    # Build the first-appearance speaker map over the raw spk values.
    spk_to_label: dict[Any, str] = {}
    for sentence in sentences:
        spk = sentence.get("spk")
        if spk is None:
            continue
        if spk not in spk_to_label:
            spk_to_label[spk] = f"spk_{len(spk_to_label) + 1:02d}"

    single_speaker = len(spk_to_label) <= 1

    segments: list[dict[str, object]] = []
    for sentence in sentences:
        text, _tags = _split_text_tags(sentence.get("text", ""))
        spk = sentence.get("spk")
        if single_speaker or spk is None:
            speaker = "self"
        else:
            speaker = spk_to_label[spk]
        segments.append(
            {
                "text": text,
                "start_ms": int(sentence.get("start", 0) or 0),
                "end_ms": int(sentence.get("end", 0) or 0),
                "speaker": speaker,
                "confidence": _confidence(sentence),
                "language": str(sentence.get("language", language) or language),
            }
        )
    return segments


def _confidence(record: dict[str, Any]) -> float | None:
    value = record.get("confidence", record.get("score"))
    if value is None:
        return None
    return float(value)


def _split_text_tags(value: object) -> tuple[str, list[str]]:
    text = str(value)
    tags = re.findall(r"<\|([^|>]+)\|>", text)
    return re.sub(r"<\|[^|>]+\|>", "", text).strip(), tags


def run_server(
    model, stdin, stdout, *, language: str, batch_size_s: int = 300,
    model_version: str = "funasr-paraformer-diarize-server", preset_spk_num: int | None = None,
) -> int:
    """Resident loop: one audio path per input line -> one result JSON per output line."""
    gen_kwargs = {"batch_size_s": batch_size_s}
    if preset_spk_num is not None:
        gen_kwargs["preset_spk_num"] = preset_spk_num
    for raw_line in stdin:
        path = raw_line.strip()
        if not path:
            continue
        if not Path(path).exists():
            # Mirror the one-shot path's terminal_exit_code=3: a missing file is permanently
            # unsupported input (it will never appear on retry), so flag it terminal. The
            # resident server can't exit per-chunk, so the terminal signal rides on the JSON
            # line as "terminal": true and the adapter maps it to TerminalPortError (parity
            # with CommandASRAdapter's exit-3 handling).
            payload = {"error": f"audio file does not exist: {path}", "terminal": True}
            stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        try:
            # FunASR/tqdm may print to stdout during inference; redirect it to stderr so only
            # our explicit JSON line reaches the one-line-per-result protocol stream.
            with contextlib.redirect_stdout(sys.stderr):
                result = model.generate(input=path, **gen_kwargs)
            payload = {
                "model_name": "paraformer-diarize",
                "model_version": model_version,
                "segments": normalize_diarized(_sentence_info(result), language=language),
            }
        except Exception as exc:  # one bad chunk must not kill the resident server (transient -> retryable)
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
