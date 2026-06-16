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

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
# Kept for back-compat (it is the default base + the chat/completions path).
GLM_ENDPOINT = f"{DEFAULT_BASE_URL}/chat/completions"
# urlopen timeouts: thinking folds long reasoning into the response, so it needs much longer.
# Generous defaults: a self-hosted glm-5.1 summarizing a big diarized session takes minutes
# (measured ~380s on a ~41k-token per-speaker prompt). Override with GLM_TIMEOUT for bigger ones.
TIMEOUT_DEFAULT = 600
TIMEOUT_THINKING = 900
ALLOWED_CLAIM_TYPES = {
    "fact", "preference", "decision", "commitment", "requirement", "observation", "todo", "relationship",
}

_TRUTHY = {"1", "true", "enabled"}


def _is_thinking(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def _extract_json(content: str) -> dict[str, object]:
    """Recover a JSON object from model content, tolerating thinking-ON inline reasoning.

    Some OpenAI-compatible servers fold the chain-of-thought into message.content before the
    JSON (and leave reasoning_content empty), so the raw content is `reasoning… {json}` which
    breaks json.loads. Try the whole string first; on failure strip a leading <think>…</think>
    block, then take the OUTERMOST balanced { … } (first '{' to its matching '}') and parse that.
    Raise (ValueError) if no JSON object can be parsed so callers fail retryably."""
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        pass
    text = content
    close = text.find("</think>")
    if text.lstrip().startswith("<think>") and close != -1:
        text = text[close + len("</think>"):]
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model content")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("no balanced JSON object found in model content")


def _post_json(
    url: str, headers: dict[str, str], body: dict[str, object], *, timeout: int = TIMEOUT_DEFAULT
) -> dict[str, object]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={**headers, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (configurable GLM endpoint)
        return json.loads(response.read().decode("utf-8"))


def call_glm(
    payload: dict[str, object],
    *,
    api_key: str,
    model: str,
    post=_post_json,
    base_url: str | None = None,
    thinking: bool = False,
    timeout: int | None = None,
) -> dict[str, object]:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base}/chat/completions"
    body = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"}, **payload}
    effective_timeout = timeout if timeout is not None else (TIMEOUT_THINKING if thinking else TIMEOUT_DEFAULT)
    if thinking:
        body["chat_template_kwargs"] = {"enable_thinking": True}
    try:
        data = post(url, {"Authorization": f"Bearer {api_key}"}, body, timeout=effective_timeout)
    except TypeError:
        # Tests inject a 3-arg post(url, headers, body); fall back to omitting the timeout kwarg.
        data = post(url, {"Authorization": f"Bearer {api_key}"}, body)
    content = data["choices"][0]["message"]["content"]
    return _extract_json(content)


def _evidence_ids(segments: list[dict]) -> set[str]:
    return {str(s["evidence_id"]) for s in segments if s.get("evidence_id")}


def _transcript_text(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        ev = s.get("evidence_id", "")
        spk = s.get("speaker", "")
        lines.append(f"[{ev}] {spk}: {s.get('text', '')}")
    return "\n".join(lines)


def _speaker_labels(segments: list[dict]) -> list[str]:
    # Distinct diarization cluster labels present in the transcript, in first-seen order.
    labels: list[str] = []
    for s in segments:
        spk = str(s.get("speaker", "")).strip()
        if spk and spk not in labels:
            labels.append(spk)
    return labels


def _transcript_by_speaker(segments: list[dict]) -> str:
    # Group lines by speaker so the model sees WHO said what, e.g. `[spk_01] [ev_3] 文本`.
    # Evidence ids stay visible because they gate evidence_refs.
    lines = []
    for s in segments:
        ev = s.get("evidence_id", "")
        spk = str(s.get("speaker", "")).strip() or "unknown"
        lines.append(f"[{spk}] [{ev}] {s.get('text', '')}")
    return "\n".join(lines)


def build_daily_messages(payload: dict) -> list[dict]:
    segments = payload.get("transcript_segments", [])
    ids = sorted(_evidence_ids(segments))
    system = (
        "你是个人上下文助手。只依据给定转写文本输出 JSON，禁止编造证据。\n"
        "语言要求：所有文本字段（summary、todos、facts、inferences 内文本、memory_candidates 的 candidate_claim）"
        "一律使用简体中文；即使转写内容为英文，也必须用简体中文表述。JSON 的键名(key)保持英文原样，不要翻译键名。\n"
        "claim_type 只能取: fact, preference, decision, commitment, requirement, observation, todo, relationship；"
        "请根据语义选择最贴切的类型，不要一律用 observation。\n"
        "质量要求：每条 memory_candidate 只表达一个不可再拆分的观点(原子化)，避免用「并且/同时」把多件事合并；"
        "candidate_claim 写成可独立审阅的完整陈述句，使审阅者无需回看转写即可判断接受或拒绝；"
        "confidence 依据证据强度赋值(明确陈述取高值，推测取低值)；去除语义重复的候选。\n"
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


def _as_list(value: object) -> list:
    # GLM (json_object mode) guarantees parseable JSON but not the requested array shape — it may
    # collapse a one-element list to a bare object or omit the field. Coerce anything non-list to
    # an empty list so a malformed shape drops to "no items" instead of crashing the whole task.
    return value if isinstance(value, list) else []


def _conf(value: object) -> float:
    # GLM may return a non-numeric confidence ("high", "0.9 (high)", null); coerce defensively
    # and clamp to [0,1] so one bad value doesn't crash the whole day's generation.
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def normalize_daily_context(raw: dict, segments: list[dict]) -> dict:
    valid = _evidence_ids(segments)
    candidates = []
    for c in _as_list(raw.get("memory_candidates")):
        if not isinstance(c, dict):
            continue  # GLM may emit a bare string / wrong shape; drop it, don't fail the day
        refs = [r for r in (c.get("evidence_source_ids") or c.get("evidence_refs") or []) if str(r) in valid]
        if not refs:
            continue  # adapter rejects empty evidence; drop rather than fail the whole day
        claim_type = c.get("claim_type") if c.get("claim_type") in ALLOWED_CLAIM_TYPES else "observation"
        candidates.append({
            "candidate_claim": str(c.get("candidate_claim", "")),
            "claim_type": claim_type,
            "confidence": _conf(c.get("confidence", 0.5)),
            "evidence_source_ids": list(dict.fromkeys(str(r) for r in refs)),
        })
    return {
        "summary": str(raw.get("summary", "")),
        "todos": [str(t) for t in _as_list(raw.get("todos"))],
        "facts": [str(f) for f in _as_list(raw.get("facts"))],
        "inferences": _normalize_inferences(_as_list(raw.get("inferences"))),
        "memory_candidates": candidates,
    }


def _normalize_inferences(items: list) -> list:
    out = []
    for item in items:
        if isinstance(item, dict) and "text" in item:
            out.append({"type": "inference", "text": str(item["text"]), "confidence": _conf(item.get("confidence", 0.5))})
        else:
            out.append(str(item))
    return out


def build_session_messages(payload: dict) -> list[dict]:
    segments = payload.get("transcript_segments", [])
    ids = sorted(_evidence_ids(segments))
    labels = _speaker_labels(segments)
    system = (
        "你是会话分析助手。只依据给定转写输出 JSON，禁止编造证据。\n"
        "本会话已做说话人聚类(diarization)：转写每行形如 [说话人] [evidence_id] 文本。请按说话人(说话人聚类标签)分别分析。\n"
        "语言要求：所有文本字段（headline、core_conclusions、per_speaker 内 viewpoints 的 text、sentiment、stance、"
        "latent_needs、open_questions）一律使用简体中文；即使转写为英文也用简体中文表述。JSON 键名保持英文不变。\n"
        "质量要求：headline 为一句中文要点；core_conclusions 是整场会话层面的结论，每条一句完整中文陈述句；\n"
        "每个 per_speaker 项对应一个说话人：viewpoints 中每条观点只表达一个不可再拆分的原子化观点，"
        "写成可独立审阅、无需回看转写即可理解的完整陈述句；sentiment(情绪)与 stance(立场/倾向)为简短中文短语；"
        "latent_needs 为该说话人潜在需求的简体中文短句列表。\n"
        "约束：speaker_cluster_id 必须是转写中出现过的说话人标签之一: "
        + ", ".join(labels) + "。\n"
        "每条 viewpoint 的 evidence_refs 只能引用下列 evidence_id 且必须非空: "
        + ", ".join(ids)
    )
    user = (
        f"会话: {payload.get('session_id')}\n"
        f"说话人标签: {', '.join(labels)}\n"
        f"转写(每行 [说话人] [evidence_id] 文本):\n{_transcript_by_speaker(segments)}\n\n"
        '输出 JSON: {"headline": str, "core_conclusions": [str], '
        '"per_speaker": [{"speaker_cluster_id": str, '
        '"viewpoints": [{"text": str, "evidence_refs": [evidence_id]}], '
        '"sentiment": str, "stance": str, "latent_needs": [str]}], '
        '"open_questions": [str]}'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _speaker_viewpoints(items: object, valid: set[str]) -> list[dict]:
    out = []
    for it in _as_list(items):
        if not isinstance(it, dict):
            continue  # a bare string / wrong shape: drop the entry, don't fail the session
        refs = [str(r) for r in (it.get("evidence_refs") or []) if str(r) in valid]
        if not refs:
            continue  # adapter rejects empty evidence_refs; drop the viewpoint (mirror memory_candidates)
        out.append({
            "text": str(it.get("text", "")),
            "evidence_refs": list(dict.fromkeys(refs)),
        })
    return out


def _normalize_per_speaker(items: object, valid: set[str]) -> list[dict]:
    out = []
    for sp in _as_list(items):
        if not isinstance(sp, dict):
            continue  # GLM may collapse the list to a bare object / emit a non-dict element
        out.append({
            "speaker_cluster_id": str(sp.get("speaker_cluster_id", "")),
            "viewpoints": _speaker_viewpoints(sp.get("viewpoints"), valid),
            "sentiment": str(sp.get("sentiment", "")),
            "stance": str(sp.get("stance", "")),
            "latent_needs": [str(n) for n in _as_list(sp.get("latent_needs"))],
        })
    return out


def normalize_session_summary(raw: dict, segments: list[dict]) -> dict:
    valid = _evidence_ids(segments)
    return {
        "headline": str(raw.get("headline", "")),
        "summary": str(raw.get("summary", "")),
        "topics": [str(t) for t in _as_list(raw.get("topics"))],
        "core_conclusions": [str(c) for c in _as_list(raw.get("core_conclusions"))],
        "per_speaker": _normalize_per_speaker(raw.get("per_speaker"), valid),
        "open_questions": [str(q) for q in _as_list(raw.get("open_questions"))],
        # The per-speaker viewpoints replace decisions/todos, but the SessionSummary/adapter
        # contract still carries decisions/todos — emit them as empty lists to stay valid.
        "decisions": [],
        "todos": [],
    }


def _load_transport():
    # Tests inject a stub transport via GLM_STUB_TRANSPORT to avoid real network calls.
    stub_path = os.environ.get("GLM_STUB_TRANSPORT")
    if not stub_path:
        return _post_json
    import importlib.util
    spec = importlib.util.spec_from_file_location("glm_stub", stub_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.post


def main() -> int:
    api_key = os.environ.get("GLM_API_KEY")
    if not api_key:
        print("GLM_API_KEY is not set", file=sys.stderr)
        return 2
    model = os.environ.get("GLM_MODEL", "glm-4-flash")
    base_url = os.environ.get("GLM_BASE_URL", DEFAULT_BASE_URL)
    thinking = _is_thinking(os.environ.get("GLM_THINKING"))
    timeout_env = os.environ.get("GLM_TIMEOUT")
    timeout = int(timeout_env) if timeout_env else None
    try:
        payload = json.loads(sys.stdin.read())
        segments = payload.get("transcript_segments", [])
        post = _load_transport()
        kwargs = {"api_key": api_key, "model": model, "post": post, "base_url": base_url,
                  "thinking": thinking, "timeout": timeout}
        if payload.get("task") == "session_summary":
            raw = call_glm({"messages": build_session_messages(payload)}, **kwargs)
            out = normalize_session_summary(raw, segments)
        else:
            raw = call_glm({"messages": build_daily_messages(payload)}, **kwargs)
            out = normalize_daily_context(raw, segments)
    except Exception as exc:  # network / JSON / GLM errors -> retryable
        print(f"GLM wrapper failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
