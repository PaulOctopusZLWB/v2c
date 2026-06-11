#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FunASR VAD and emit Personal Context Node VAD JSON.")
    parser.add_argument("audio_path", type=Path)
    parser.add_argument("--model", default="fsmn-vad")
    parser.add_argument("--model-revision", default=None)
    args = parser.parse_args()

    if not args.audio_path.exists():
        print(f"audio file does not exist: {args.audio_path}", file=sys.stderr)
        return 2

    try:
        from funasr import AutoModel
    except ImportError:
        print(
            "FunASR is not installed. Install it in the uv/Docker runtime that runs this wrapper.",
            file=sys.stderr,
        )
        return 2

    model_kwargs: dict[str, Any] = {"model": args.model}
    if args.model_revision:
        model_kwargs["model_revision"] = args.model_revision
    model = AutoModel(**model_kwargs)
    raw_result = model.generate(input=str(args.audio_path))
    print(json.dumps({"ranges": _normalize_ranges(raw_result)}, ensure_ascii=False, sort_keys=True))
    return 0


def _normalize_ranges(raw_result: Any) -> list[dict[str, int]]:
    records = raw_result if isinstance(raw_result, list) else [raw_result]
    ranges: list[dict[str, int]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        values = record.get("value", record.get("ranges", record.get("speech_ranges", [])))
        if not isinstance(values, list):
            continue
        for item in values:
            normalized = _normalize_range(item)
            if normalized is not None:
                ranges.append(normalized)
    return ranges


def _normalize_range(item: object) -> dict[str, int] | None:
    if isinstance(item, dict):
        start_ms = int(item["start_ms"])
        end_ms = int(item["end_ms"])
    elif isinstance(item, list) and len(item) >= 2:
        start_ms = int(item[0])
        end_ms = int(item[1])
    else:
        return None
    if end_ms <= start_ms:
        raise ValueError(f"invalid VAD range: start_ms={start_ms} end_ms={end_ms}")
    return {"start_ms": start_ms, "end_ms": end_ms}


if __name__ == "__main__":
    raise SystemExit(main())
