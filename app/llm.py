"""A small OpenAI-compatible chat client (standard library only).

Works with any endpoint that speaks POST {base}/chat/completions: Google's
Gemini OpenAI endpoint (the default), OpenAI, OpenRouter, Groq, a local
Ollama or LM Studio, vLLM...

`structured()` asks for one JSON object through native tool calling - the
model fills in a function's arguments against a JSON schema - and falls back
to reading JSON out of the text for endpoints without tool support.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import config


class LLMError(RuntimeError):
    pass


_RETRY_STATUS = (429, 500, 502, 503, 504)
# a free plan answers 429 "retry in 46s": wait that long (up to this) and go on
RATE_WAIT_MAX_S = 70.0
_rate_lock = __import__("threading").Lock()
_sent: list[float] = []            # when the last requests went out
_rpm: int | None = None            # requests a minute the service allows (learned from a 429)


def _retry_after(detail: str) -> float | None:
    """Seconds to wait from a 429 body ("retryDelay": "46s" / "retry in 46.8s")."""
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', detail) or re.search(r"retry in (\d+(?:\.\d+)?)\s*s", detail)
    return float(m.group(1)) if m else None


def _learn_rpm(detail: str) -> None:
    global _rpm
    m = re.search(r'"quotaValue"\s*:\s*"(\d+)"', detail) or re.search(r"limit:\s*(\d+)", detail)
    if m and "minute" in detail.lower() or (m and "PerMinute" in detail):
        _rpm = max(1, int(m.group(1)))


def _pace() -> None:
    """Keep under the service's requests-a-minute (once it has told us)."""
    with _rate_lock:
        now = time.monotonic()
        del _sent[:len([t for t in _sent if now - t > 60.0])]
        if _rpm and len(_sent) >= _rpm:
            wait = 60.0 - (now - _sent[0]) + 0.3
            if 0 < wait <= RATE_WAIT_MAX_S:
                time.sleep(wait)
                now = time.monotonic()
                del _sent[:len([t for t in _sent if now - t > 60.0])]
        _sent.append(time.monotonic())


def available() -> bool:
    return bool(config.LLM_API_KEY)


def chat(messages: list[dict], tools: list[dict] | None = None,
         tool_choice=None, temperature: float = 0.3) -> dict:
    """One chat completion; returns the assistant message dict."""
    if not config.LLM_API_KEY:
        raise LLMError("no API key configured")
    body: dict = {"model": config.LLM_MODEL, "messages": messages,
                  "temperature": temperature}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(4):
        _pace()
        request = urllib.request.Request(
            f"{config.LLM_BASE_URL}/chat/completions", data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {config.LLM_API_KEY}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            full = exc.read().decode("utf-8", "replace")
            detail = full[:400]
            if exc.code == 429:
                _learn_rpm(full)
                wait = _retry_after(full)
                if wait is not None and wait <= RATE_WAIT_MAX_S and attempt < 3:
                    time.sleep(wait + 0.5)             # the free plan's limit: wait it out
                    continue
                last = LLMError("the AI service's limit is reached"
                                + (f" ({_rpm} requests a minute on this plan)" if _rpm else "")
                                + (f" - try again in {int(wait)} s" if wait else "") + ".")
                raise last from exc
            last = LLMError(f"AI service HTTP {exc.code}: {detail}")
            if exc.code in _RETRY_STATUS and attempt == 0:
                time.sleep(1.5)
                continue
            raise last from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"cannot reach the AI service at {config.LLM_BASE_URL}: {exc}") from exc
    else:
        raise last or LLMError("AI service failed")
    try:
        return payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected AI response: {json.dumps(payload)[:300]}") from exc


def extract_json(text: str) -> dict:
    """The first JSON object in a reply (bare, or in a ``` fence)."""
    body = str(text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    if not body.startswith("{"):
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the AI reply contains no JSON object")
        body = body[start:end + 1]
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("the AI reply JSON is not an object")
    return data


def structured(messages: list[dict], name: str, description: str,
               schema: dict, temperature: float = 0.3) -> dict:
    """Ask for one object matching `schema`, via a forced tool call.

    Endpoints that ignore tools answer in text; that text is parsed as JSON
    instead, so the caller always gets a dict or an exception.
    """
    tool = {"type": "function", "function": {
        "name": name, "description": description, "parameters": schema}}
    try:
        msg = chat(messages, tools=[tool],
                   tool_choice={"type": "function", "function": {"name": name}},
                   temperature=temperature)
    except LLMError as exc:
        # Some endpoints reject a forced tool_choice; ask again plainly.
        if "tool" not in str(exc).lower():
            raise
        msg = chat(messages, temperature=temperature)
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, dict):
            return args
        if isinstance(args, str) and args.strip():
            return json.loads(args)
    return extract_json(str(msg.get("content") or ""))
