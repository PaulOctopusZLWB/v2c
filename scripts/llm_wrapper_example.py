#!/usr/bin/env python3
"""Example command-LLM wrapper contract.

The wrapper receives JSON on stdin:
`{"day": "YYYY-MM-DD", "transcript_segments": [...]}`.
It must print normalized daily context JSON to stdout.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read())
    segments = payload.get("transcript_segments", [])
    evidence_source_ids = [segments[0]["segment_id"]] if segments else []
    print(
        json.dumps(
            {
                "summary": f"示例 LLM 摘要：{payload.get('day')}",
                "todos": [],
                "facts": [],
                "inferences": [],
                "memory_candidates": [
                    {
                        "candidate_claim": "示例 LLM 候选：继续完善本地上下文系统。",
                        "claim_type": "observation",
                        "confidence": 0.5,
                        "evidence_source_ids": evidence_source_ids,
                    }
                ]
                if evidence_source_ids
                else [],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
