#!/usr/bin/env python3
"""Example command-ASR wrapper contract.

Replace the body of `main()` with a real FunASR/SenseVoice call. The wrapper
must accept one audio path argument and print the normalized ASR JSON expected
by `CommandASRAdapter`.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: asr_wrapper_example.py AUDIO_PATH", file=sys.stderr)
        return 2
    audio_path = sys.argv[1]
    print(
        json.dumps(
            {
                "model_name": "example-wrapper",
                "model_version": "replace-with-funasr-sensevoice-version",
                "segments": [
                    {
                        "text": f"示例 ASR 输出：{audio_path}",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "confidence": None,
                        "language": "zh",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
