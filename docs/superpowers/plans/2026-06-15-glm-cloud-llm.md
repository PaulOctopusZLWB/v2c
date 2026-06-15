# GLM Cloud LLM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the official Zhipu GLM cloud API as the LLM backend through a local command wrapper, while keeping raw audio local and rule_based as the offline fallback.

**Architecture:** No core changes. Add a `scripts/glm_llm_wrapper.py` that conforms to the existing `CommandLLMAdapter` stdin/stdout contract (text-only transcript JSON in, normalized daily_context / session_summary JSON out). The pipeline reaches it via `llm_backend = "command"` + `llm_command`. The API key comes from the `GLM_API_KEY` environment variable, never a config file. The wrapper is split into importable, network-free functions plus a thin transport seam so it is fully unit-testable.

**Tech Stack:** Python 3.11+ stdlib only (`urllib.request`, `json`), the existing `CommandLLMAdapter`, pytest. No new runtime dependency.

---

## Contract (already enforced by `src/personal_context_node/adapters/llm/command.py`)

The adapter sends one JSON object on stdin and expects one JSON object on stdout.

- **daily_context** in: `{"task": "daily_context", "day": "YYYY-MM-DD", "transcript_segments": [ {"segment_id","evidence_id","text","speaker",...}, ... ]}`
  out: `{"summary": str, "todos": [str], "facts": [str], "inferences": [str | {"type":"inference","text":str,"confidence":float}], "memory_candidates": [ {"candidate_claim": str, "claim_type": <one of fact|preference|decision|commitment|requirement|observation|todo|relationship>, "confidence": float, "evidence_source_ids": [str], "subject": {"type","id","label"}?} ]}`
- **session_summary** in: `{"task": "session_summary", "session_id": str, "transcript_segments": [...]}`
  out: `{"headline": str, "summary": str, "topics": [str], "decisions": [{"text": str, "evidence_refs": [str]}], "todos": [{"text": str, "owner": str, "evidence_refs": [str]}], "open_questions": [str]}`

Non-negotiable rules the wrapper must honor:
- Every `evidence_source_ids` / `evidence_refs` value MUST be an `evidence_id` that appears in the input segments (the adapter rejects unknown/blank refs and aborts the whole generation).
- `claim_type` MUST be one of the eight allowed literals; anything else is rejected.
- On any failure (missing key, GLM error, unparseable JSON) the wrapper exits non-zero so `CommandLLMAdapter` raises `RetryablePortError` and the task is retried, never silently committing partial output.

## File Structure

- Create `scripts/glm_llm_wrapper.py`: importable functions (`build_daily_messages`, `build_session_messages`, `normalize_daily_context`, `normalize_session_summary`, `call_glm`) + a `main()` that reads stdin, dispatches on `task`, and prints contract JSON.
- Create `tests/test_glm_llm_wrapper.py`: unit tests for normalization/validation with an injected fake transport; one end-to-end `subprocess` test of the script with a stubbed GLM via a fake transport module.
- Modify `config/local.example.toml`: add commented `[llm] command = ...` opt-in showing the GLM wrapper.

## Task 1: Wrapper skeleton + GLM transport seam

**Files:**
- Create: `scripts/glm_llm_wrapper.py`
- Create: `tests/test_glm_llm_wrapper.py`

- [ ] **Step 1: Write the failing transport test**

Create `tests/test_glm_llm_wrapper.py`:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("glm_llm_wrapper", Path("scripts/glm_llm_wrapper.py"))
glm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(glm)


def test_call_glm_extracts_message_content_json() -> None:
    captured = {}

    def fake_post(url, headers, body):
        captured["url"] = url
        captured["auth"] = headers["Authorization"]
        captured["body"] = body
        return {"choices": [{"message": {"content": '{"summary": "ok"}'}}]}

    result = glm.call_glm({"messages": []}, api_key="sk-test", model="glm-4-flash", post=fake_post)

    assert result == {"summary": "ok"}
    assert captured["auth"] == "Bearer sk-test"
    assert "chat/completions" in captured["url"]
    assert captured["body"]["model"] == "glm-4-flash"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_glm_llm_wrapper.py::test_call_glm_extracts_message_content_json -q`
Expected: FAIL — `scripts/glm_llm_wrapper.py` does not exist.

- [ ] **Step 3: Write the wrapper skeleton + `call_glm`**

Create `scripts/glm_llm_wrapper.py`:

```python
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
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_glm_llm_wrapper.py::test_call_glm_extracts_message_content_json -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/glm_llm_wrapper.py tests/test_glm_llm_wrapper.py
git commit -m "feat(llm): GLM transport seam for command wrapper"
```

## Task 2: Daily-context prompt + normalization

**Files:**
- Modify: `scripts/glm_llm_wrapper.py`
- Modify: `tests/test_glm_llm_wrapper.py`

- [ ] **Step 1: Write the failing normalization test**

Add to `tests/test_glm_llm_wrapper.py`:

```python
def test_normalize_daily_context_constrains_claim_type_and_evidence() -> None:
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "数据不出本机。"}]
    raw = {
        "summary": "讨论本地部署。",
        "todos": ["继续接入模型"],
        "facts": ["音频本地处理"],
        "inferences": [{"type": "inference", "text": "关注证据链", "confidence": 0.7}],
        "memory_candidates": [
            {"candidate_claim": "用户要求音频本地处理。", "claim_type": "SECRET", "confidence": 0.9,
             "evidence_source_ids": ["ev_1", "ev_unknown"]},
            {"candidate_claim": "无证据的候选", "claim_type": "fact", "confidence": 0.5, "evidence_source_ids": []},
        ],
    }

    out = glm.normalize_daily_context(raw, segments)

    assert out["summary"] == "讨论本地部署。"
    # invalid claim_type coerced to the safe default 'observation'
    assert out["memory_candidates"][0]["claim_type"] == "observation"
    # unknown evidence id dropped, valid one kept
    assert out["memory_candidates"][0]["evidence_source_ids"] == ["ev_1"]
    # candidate with no surviving evidence is dropped entirely (adapter would reject it)
    assert len(out["memory_candidates"]) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_glm_llm_wrapper.py::test_normalize_daily_context_constrains_claim_type_and_evidence -q`
Expected: FAIL — `normalize_daily_context` not defined.

- [ ] **Step 3: Implement prompt builder + normalizer**

Append to `scripts/glm_llm_wrapper.py`:

```python
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
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_glm_llm_wrapper.py::test_normalize_daily_context_constrains_claim_type_and_evidence -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/glm_llm_wrapper.py tests/test_glm_llm_wrapper.py
git commit -m "feat(llm): GLM daily-context prompt and contract normalization"
```

## Task 3: Session-summary prompt + normalization

**Files:**
- Modify: `scripts/glm_llm_wrapper.py`
- Modify: `tests/test_glm_llm_wrapper.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_glm_llm_wrapper.py`:

```python
def test_normalize_session_summary_drops_decisions_without_known_evidence() -> None:
    segments = [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "继续本地 ASR。"}]
    raw = {
        "headline": "本地 ASR 推进", "summary": "讨论本地转写。", "topics": ["asr"],
        "decisions": [{"text": "继续本地 ASR", "evidence_refs": ["ev_1"]},
                      {"text": "无证据决定", "evidence_refs": ["ev_x"]}],
        "todos": [{"text": "完成 smoke", "owner": "self", "evidence_refs": ["ev_1"]}],
        "open_questions": ["是否需要备选模型"],
    }

    out = glm.normalize_session_summary(raw, segments)

    assert out["headline"] == "本地 ASR 推进"
    assert [d["text"] for d in out["decisions"]] == ["继续本地 ASR"]  # ev_x dropped
    assert out["todos"][0]["owner"] == "self"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_glm_llm_wrapper.py::test_normalize_session_summary_drops_decisions_without_known_evidence -q`
Expected: FAIL — `normalize_session_summary` not defined.

- [ ] **Step 3: Implement**

Append to `scripts/glm_llm_wrapper.py`:

```python
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
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_glm_llm_wrapper.py::test_normalize_session_summary_drops_decisions_without_known_evidence -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/glm_llm_wrapper.py tests/test_glm_llm_wrapper.py
git commit -m "feat(llm): GLM session-summary prompt and normalization"
```

## Task 4: `main()` dispatch + key handling + end-to-end script test

**Files:**
- Modify: `scripts/glm_llm_wrapper.py`
- Modify: `tests/test_glm_llm_wrapper.py`

- [ ] **Step 1: Write the failing end-to-end test**

Add to `tests/test_glm_llm_wrapper.py`:

```python
import json
import os
import subprocess
import sys


def test_main_emits_contract_json_via_stubbed_transport(tmp_path) -> None:
    # A fake transport module that returns canned GLM responses; injected via env so the
    # script runs as a real subprocess (the path CommandLLMAdapter uses).
    stub = tmp_path / "glm_stub.py"
    stub.write_text(
        "def post(url, headers, body):\n"
        "    return {'choices': [{'message': {'content': '"
        '{"summary":"日报","todos":[],"facts":[],"inferences":[],'
        '"memory_candidates":[{\\"candidate_claim\\":\\"c\\",\\"claim_type\\":\\"fact\\",'
        '\\"confidence\\":0.9,\\"evidence_source_ids\\":[\\"ev_1\\"]}]}'
        "'}}]}\n",
        encoding="utf-8",
    )
    payload = {"task": "daily_context", "day": "2026-06-07",
               "transcript_segments": [{"segment_id": "seg_1", "evidence_id": "ev_1", "text": "x"}]}
    env = {**os.environ, "GLM_API_KEY": "sk-test", "GLM_STUB_TRANSPORT": str(stub)}

    proc = subprocess.run([sys.executable, "scripts/glm_llm_wrapper.py"], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)

    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["summary"] == "日报"
    assert out["memory_candidates"][0]["evidence_source_ids"] == ["ev_1"]


def test_main_fails_retryable_without_api_key() -> None:
    env = {k: v for k, v in os.environ.items() if k != "GLM_API_KEY"}
    proc = subprocess.run([sys.executable, "scripts/glm_llm_wrapper.py"],
                          input='{"task":"daily_context","day":"2026-06-07","transcript_segments":[]}',
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0
    assert "GLM_API_KEY" in proc.stderr
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_glm_llm_wrapper.py -q -k main`
Expected: FAIL — `main()` does not dispatch / read the stub yet.

- [ ] **Step 3: Implement `main()`**

Append to `scripts/glm_llm_wrapper.py`:

```python
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
    try:
        payload = json.loads(sys.stdin.read())
        segments = payload.get("transcript_segments", [])
        post = _load_transport()
        if payload.get("task") == "session_summary":
            raw = call_glm({"messages": build_session_messages(payload)}, api_key=api_key, model=model, post=post)
            out = normalize_session_summary(raw, segments)
        else:
            raw = call_glm({"messages": build_daily_messages(payload)}, api_key=api_key, model=model, post=post)
            out = normalize_daily_context(raw, segments)
    except Exception as exc:  # network / JSON / GLM errors -> retryable
        print(f"GLM wrapper failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_glm_llm_wrapper.py -q`
Expected: PASS (all tasks' tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/glm_llm_wrapper.py tests/test_glm_llm_wrapper.py
git commit -m "feat(llm): GLM wrapper main dispatch + key guard"
```

## Task 5: Wire-up docs + live smoke

**Files:**
- Modify: `config/local.example.toml`

- [ ] **Step 1: Add the opt-in config comment**

In `config/local.example.toml`, under `[llm]`, add (keep `backend = "rule_based"` as the default):

```toml
# To use the official Zhipu GLM cloud model (text-only), export GLM_API_KEY and set:
#   backend = "command"
#   command = "python3 scripts/glm_llm_wrapper.py"
# GLM_MODEL defaults to glm-4-flash; raw audio is never sent (CommandLLMAdapter strips paths).
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 3: Live smoke (manual, requires real key)**

```bash
export GLM_API_KEY=...your key...
echo '{"task":"daily_context","day":"2026-06-07","transcript_segments":[{"segment_id":"seg_1","evidence_id":"ev_1","text":"数据不出本机。"}]}' \
  | python3 scripts/glm_llm_wrapper.py
# Expect a JSON object with summary / memory_candidates whose evidence_source_ids == ["ev_1"].
uv run pcn summarize --config config/local.toml --day 2026-06-07 \
  --llm-backend command --llm-command "python3 scripts/glm_llm_wrapper.py"
```

- [ ] **Step 4: Commit**

```bash
git add config/local.example.toml
git commit -m "docs(llm): document GLM command backend opt-in"
```

## Self-Review

- **Spec coverage:** GLM wrapper (Tasks 1-4), env key only (Task 4 key guard, no config field), reuse command adapter (contract honored throughout), rule_based fallback preserved (config default unchanged, Task 5). ✓
- **Placeholders:** none — every step has full code/commands.
- **Type consistency:** `call_glm`, `build_daily_messages`/`build_session_messages`, `normalize_daily_context`/`normalize_session_summary`, `_evidence_ids`, `_load_transport` are defined once and referenced consistently; `ALLOWED_CLAIM_TYPES` matches `ClaimType` in `core/ports/llm.py`.
