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
