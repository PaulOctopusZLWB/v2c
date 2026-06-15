#!/usr/bin/env python3
"""Command-LLM wrapper that backs daily_context / session_summary with the Zhipu GLM API.

Reads one JSON object on stdin (see CommandLLMAdapter), calls GLM, prints normalized
contract JSON on stdout. API key comes from GLM_API_KEY. Exit codes: 0 success;
non-zero = retryable failure (missing key / GLM error / unparseable output)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ALLOWED_CLAIM_TYPES = {
    "fact", "preference", "decision", "commitment", "requirement", "observation", "todo", "relationship",
}


def _post_json(url: str, headers: dict[str, str], body: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={**headers, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 (fixed GLM endpoint)
        return json.loads(response.read().decode("utf-8"))


def call_glm(payload: dict[str, object], *, api_key: str, model: str, post=_post_json) -> dict[str, object]:
    body = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"}, **payload}
    data = post(GLM_ENDPOINT, {"Authorization": f"Bearer {api_key}"}, body)
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def _evidence_ids(segments: list[dict]) -> set[str]:
    return {str(s["evidence_id"]) for s in segments if s.get("evidence_id")}


def _transcript_text(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        ev = s.get("evidence_id", "")
        spk = s.get("speaker", "")
        lines.append(f"[{ev}] {spk}: {s.get('text', '')}")
    return "\n".join(lines)


def build_daily_messages(payload: dict) -> list[dict]:
    segments = payload.get("transcript_segments", [])
    ids = sorted(_evidence_ids(segments))
    system = (
        "你是个人上下文助手。只依据给定转写文本输出 JSON，禁止编造证据。"
        "claim_type 只能取: fact, preference, decision, commitment, requirement, observation, todo, relationship。"
        "memory_candidates 的 evidence_source_ids 只能引用下列 evidence_id 之一，且必须非空: "
        + ", ".join(ids)
    )
    user = (
        f"日期: {payload.get('day')}\n转写(每行 [evidence_id] 说话人: 文本):\n{_transcript_text(segments)}\n\n"
        '输出 JSON: {"summary": str, "todos": [str], "facts": [str], '
        '"inferences": [str], "memory_candidates": '
        '[{"candidate_claim": str, "claim_type": str, "confidence": 0..1, "evidence_source_ids": [evidence_id]}]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def normalize_daily_context(raw: dict, segments: list[dict]) -> dict:
    valid = _evidence_ids(segments)
    candidates = []
    for c in raw.get("memory_candidates", []) or []:
        refs = [r for r in (c.get("evidence_source_ids") or c.get("evidence_refs") or []) if str(r) in valid]
        if not refs:
            continue  # adapter rejects empty evidence; drop rather than fail the whole day
        claim_type = c.get("claim_type") if c.get("claim_type") in ALLOWED_CLAIM_TYPES else "observation"
        candidates.append({
            "candidate_claim": str(c.get("candidate_claim", "")),
            "claim_type": claim_type,
            "confidence": float(c.get("confidence", 0.5)),
            "evidence_source_ids": list(dict.fromkeys(str(r) for r in refs)),
        })
    return {
        "summary": str(raw.get("summary", "")),
        "todos": [str(t) for t in raw.get("todos", []) or []],
        "facts": [str(f) for f in raw.get("facts", []) or []],
        "inferences": _normalize_inferences(raw.get("inferences", []) or []),
        "memory_candidates": candidates,
    }


def _normalize_inferences(items: list) -> list:
    out = []
    for item in items:
        if isinstance(item, dict) and "text" in item:
            out.append({"type": "inference", "text": str(item["text"]), "confidence": float(item.get("confidence", 0.5))})
        else:
            out.append(str(item))
    return out


def build_session_messages(payload: dict) -> list[dict]:
    segments = payload.get("transcript_segments", [])
    ids = sorted(_evidence_ids(segments))
    system = (
        "你是会话纪要助手。只依据转写输出 JSON。decisions/todos 的 evidence_refs 只能引用下列 evidence_id 且非空: "
        + ", ".join(ids)
    )
    user = (
        f"会话: {payload.get('session_id')}\n转写:\n{_transcript_text(segments)}\n\n"
        '输出 JSON: {"headline": str, "summary": str, "topics": [str], '
        '"decisions": [{"text": str, "evidence_refs": [evidence_id]}], '
        '"todos": [{"text": str, "owner": str, "evidence_refs": [evidence_id]}], "open_questions": [str]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def normalize_session_summary(raw: dict, segments: list[dict]) -> dict:
    valid = _evidence_ids(segments)

    def keep(items, owner=False):
        out = []
        for it in items or []:
            refs = [str(r) for r in (it.get("evidence_refs") or []) if str(r) in valid]
            if not refs:
                continue
            row = {"text": str(it.get("text", "")), "evidence_refs": refs}
            if owner:
                row["owner"] = str(it.get("owner", "self"))
            out.append(row)
        return out

    return {
        "headline": str(raw.get("headline", "")),
        "summary": str(raw.get("summary", "")),
        "topics": [str(t) for t in raw.get("topics", []) or []],
        "decisions": keep(raw.get("decisions")),
        "todos": keep(raw.get("todos"), owner=True),
        "open_questions": [str(q) for q in raw.get("open_questions", []) or []],
    }
