"""Tiny OpenAI-compatible chat client (standard library only).

Works with OpenAI, OpenRouter, Groq, Together, Ollama (/v1), LM Studio,
vLLM, ... anything that speaks POST {base}/chat/completions with tools.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


class LLMError(RuntimeError):
    pass


def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    if not config.LLM_API_KEY:
        raise LLMError("no API key configured")

    body: dict = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0.4,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    request = urllib.request.Request(
        f"{config.LLM_BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.LLM_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"cannot reach LLM at {config.LLM_BASE_URL}: {exc}") from exc

    try:
        return payload["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected LLM response: {json.dumps(payload)[:300]}") from exc


def available() -> bool:
    return bool(config.LLM_API_KEY)
