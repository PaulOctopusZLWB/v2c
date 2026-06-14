#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from funasr_sensevoice_wrapper import _normalize_segments


JSONL_NAME = "sample_transcripts.jsonl"
CSV_NAME = "sample_transcript_segments.csv"
TEXT_NAME = "sample_transcripts.txt"
ERROR_NAME = "sample_transcript_errors.jsonl"
CSV_FIELDS = [
    "file_name",
    "segment_index",
    "start_ms",
    "end_ms",
    "language",
    "speaker",
    "text",
    "tags_json",
    "confidence",
    "source_path",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch transcribe sample_data WAV files with one SenseVoice model instance.")
    parser.add_argument("--source-dir", type=Path, default=Path("sample_data"), help="Directory containing WAV files.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/sample_transcripts"),
        help="Directory where transcript files are written.",
    )
    parser.add_argument("--model", default="iic/SenseVoiceSmall", help="FunASR/SenseVoice model id or local path.")
    parser.add_argument("--vad-model", default=None, help="Optional FunASR VAD model id.")
    parser.add_argument("--model-version", default="funasr-sensevoice-local")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--batch-size-s", type=int, default=300)
    parser.add_argument("--force", action="store_true", help="Rewrite output files and transcribe every WAV again.")
    args = parser.parse_args()

    if not args.source_dir.exists() or not args.source_dir.is_dir():
        print(f"source directory does not exist: {args.source_dir}", file=sys.stderr)
        return 2

    audio_paths = sorted(path for path in args.source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".wav")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = _output_paths(args.output_dir)
    if args.force:
        _remove_outputs(paths.values())

    completed = set() if args.force else _completed_files(paths["jsonl"])

    try:
        with contextlib.redirect_stdout(sys.stderr):
            from funasr import AutoModel
    except ImportError:
        print("FunASR is not installed. Install it with `uv sync --extra funasr` or run inside the FunASR runtime.", file=sys.stderr)
        return 2

    model_kwargs: dict[str, Any] = {"model": args.model}
    if args.vad_model:
        model_kwargs["vad_model"] = args.vad_model
    with contextlib.redirect_stdout(sys.stderr):
        model = AutoModel(**model_kwargs)

    succeeded = 0
    failed = 0
    skipped = 0
    with paths["jsonl"].open("a", encoding="utf-8") as jsonl_file, paths["text"].open(
        "a", encoding="utf-8"
    ) as text_file, paths["errors"].open("a", encoding="utf-8") as error_file, _open_csv(paths["csv"]) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        if csv_file.tell() == 0:
            writer.writeheader()
        for index, audio_path in enumerate(audio_paths, start=1):
            if audio_path.name in completed:
                skipped += 1
                print(f"[{index}/{len(audio_paths)}] skip {audio_path.name}", file=sys.stderr, flush=True)
                continue

            print(f"[{index}/{len(audio_paths)}] transcribe {audio_path.name}", file=sys.stderr, flush=True)
            try:
                segments = _transcribe_audio(
                    model=model,
                    audio_path=audio_path,
                    language=args.language,
                    batch_size_s=args.batch_size_s,
                )
            except Exception as exc:
                failed += 1
                _write_jsonl(
                    error_file,
                    {
                        "source_path": str(audio_path),
                        "file_name": audio_path.name,
                        "error": str(exc),
                    },
                )
                continue

            transcript_text = " ".join(str(segment["text"]) for segment in segments if str(segment.get("text", "")).strip())
            _write_jsonl(
                jsonl_file,
                {
                    "source_path": str(audio_path),
                    "file_name": audio_path.name,
                    "model_name": "sensevoice",
                    "model_version": args.model_version,
                    "text": transcript_text,
                    "segments": segments,
                },
            )
            if transcript_text:
                text_file.write(f"{audio_path.name}\t{transcript_text}\n")
                text_file.flush()
            _write_csv_segments(writer=writer, csv_file=csv_file, audio_path=audio_path, segments=segments)
            succeeded += 1

    print(
        " ".join(
            [
                f"files_found={len(audio_paths)}",
                f"files_skipped={skipped}",
                f"files_transcribed={succeeded}",
                f"files_failed={failed}",
                f"jsonl={paths['jsonl']}",
                f"csv={paths['csv']}",
                f"text={paths['text']}",
                f"errors={paths['errors']}",
            ]
        )
    )
    return 0 if failed == 0 else 1


def _output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "jsonl": output_dir / JSONL_NAME,
        "csv": output_dir / CSV_NAME,
        "text": output_dir / TEXT_NAME,
        "errors": output_dir / ERROR_NAME,
    }


def _remove_outputs(paths: Any) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _completed_files(jsonl_path: Path) -> set[str]:
    if not jsonl_path.exists():
        return set()
    completed: set[str] = set()
    for line_number, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            print(f"ignoring invalid JSONL line {line_number} in {jsonl_path}", file=sys.stderr)
            continue
        file_name = payload.get("file_name") if isinstance(payload, dict) else None
        if isinstance(file_name, str) and file_name:
            completed.add(file_name)
    return completed


def _open_csv(path: Path) -> TextIO:
    return path.open("a", encoding="utf-8", newline="")


def _transcribe_audio(*, model: Any, audio_path: Path, language: str, batch_size_s: int) -> list[dict[str, object]]:
    with contextlib.redirect_stdout(sys.stderr):
        raw_result = model.generate(
            input=str(audio_path),
            language=language,
            use_itn=True,
            batch_size_s=batch_size_s,
        )
    return _normalize_segments(raw_result)


def _write_jsonl(file: TextIO, payload: dict[str, object]) -> None:
    file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    file.flush()


def _write_csv_segments(
    *,
    writer: csv.DictWriter[str],
    csv_file: TextIO,
    audio_path: Path,
    segments: list[dict[str, object]],
) -> None:
    for segment_index, segment in enumerate(segments):
        writer.writerow(
            {
                "file_name": audio_path.name,
                "segment_index": segment_index,
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "language": segment["language"],
                "speaker": segment["speaker"],
                "text": segment["text"],
                "tags_json": json.dumps(segment["tags"], ensure_ascii=False),
                "confidence": "" if segment["confidence"] is None else segment["confidence"],
                "source_path": str(audio_path),
            }
        )
    csv_file.flush()


if __name__ == "__main__":
    raise SystemExit(main())
