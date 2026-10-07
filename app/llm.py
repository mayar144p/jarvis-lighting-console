"""A small OpenAI-compatible chat client (standard library only).

Works with any endpoint that speaks POST {base}/chat/completions: Google's
Gemini OpenAI endpoint (the default), OpenAI, OpenRouter, Groq, a local
Ollama or LM Studio, vLLM...

Two AIs, one switch (backlog A12): **online** (Gemini by default - the key
from Settings -> AI or .env), **local** (an AI running on this computer:
the desk's own, or Ollama / LM Studio - unlimited, no internet), and
**auto** - online first; when it hits its limit, has no internet or doesn't
answer, the desk carries on with the local AI and says so (`last_note`).
The choice and the key are kept in DATA/ai.json (never in a show, never in
a bug report).

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


# ---------------------------------------------------------------- the switch
MODES = ("online", "local", "auto")
LOCAL_URL = "http://127.0.0.1:11434/v1"      # Ollama's; the desk's own AI sets its own
last_note = ""                              # "Gemini limit reached - using the offline AI"
last_used = ""                              # "online" / "local"
_local_seen: dict = {"t": 0.0, "models": None, "url": ""}


def _settings_file():
    return config.DATA / "ai.json"


def settings() -> dict:
    """{mode, key, model, local_url, local_model} - the desk's own, over .env."""
    try:
        got = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    key = str(got.get("key") or config.LLM_API_KEY or "")
    mode = got.get("mode") if got.get("mode") in MODES else ("auto" if key else "local")
    return {"mode": mode, "key": key, "model": str(got.get("model") or config.LLM_MODEL),
            "local_url": str(got.get("local_url") or LOCAL_URL).rstrip("/"),
            "local_model": str(got.get("local_model") or "")}


def save_settings(mode=None, key=None, model=None, local_url=None, local_model=None) -> dict:
    """Change what was given; the key is kept, never shown back."""
    try:
        cur = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    if mode is not None:
        if mode not in MODES:
            raise ValueError("mode is online, local or auto")
        cur["mode"] = mode
    for name, val in (("key", key), ("model", model), ("local_model", local_model)):
        if val is not None:
            cur[name] = str(val).strip()
    if local_url is not None:
        url = str(local_url).strip().rstrip("/")
        if url and not re.match(r"^https?://[\w.\-]+(:\d+)?(/[\w.\-/]*)?$", url):
            raise ValueError("the local AI's address looks like http://127.0.0.1:11434/v1")
        cur["local_url"] = url
    config.DATA.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps(cur, indent=1), encoding="utf-8")
    _local_seen["t"] = 0.0
    return public()


def _online() -> dict | None:
    st = settings()
    return {"name": "online", "url": config.LLM_BASE_URL, "key": st["key"], "model": st["model"]} \
        if st["key"] else None


def local_models(force: bool = False) -> list[str] | None:
    """The models the local AI offers, or None when none is running (asked
    at most every 10 s: it is on the way of every request)."""
    st = settings()
    url = _local_url() or st["local_url"]
    if not force and _local_seen["url"] == url and time.monotonic() - _local_seen["t"] < 10:
        return _local_seen["models"]
    models = None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{url}/models"), timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]
    except (OSError, ValueError, urllib.error.URLError):
        models = None
    _local_seen.update(t=time.monotonic(), models=models, url=url)
    return models


def _local_url() -> str:
    """The desk's own local AI, when it is running (app/localai.py)."""
    try:
        from . import localai
        return localai.url() or ""
    except ImportError:
        return ""


def _local() -> dict | None:
    st = settings()
    models = local_models()
    if not models:
        return None
    url = _local_url() or st["local_url"]
    model = st["local_model"] if st["local_model"] in models else models[0]
    return {"name": "local", "url": url, "key": "local", "model": model}


def available() -> bool:
    st = settings()
    if st["mode"] == "online":
        return _online() is not None
    if st["mode"] == "local":
        return _local() is not None or _can_start_local()
    return _online() is not None or _local() is not None or _can_start_local()


def _can_start_local() -> bool:
    try:
        from . import localai
        return localai.ready()
    except ImportError:
        return False


def public() -> dict:
    """What the screen may know: never the key itself."""
    st = settings()
    models = local_models()
    return {"mode": st["mode"], "has_key": bool(st["key"]), "model": st["model"],
            "local_url": st["local_url"], "local_model": st["local_model"],
            "local_running": bool(models), "local_models": models or [],
            "available": available(), "last_used": last_used, "last_note": last_note}


def chat(messages: list[dict], tools: list[dict] | None = None,
         tool_choice=None, temperature: float = 0.3) -> dict:
    """One chat completion through the switch; returns the assistant message."""
    global last_note, last_used
    mode = settings()["mode"]
    online = _online() if mode != "local" else None
    if mode != "local" and online is not None:
        try:
            # Auto doesn't sit out a "retry in 46 s": the offline AI takes over
            msg = _chat(online, messages, tools, tool_choice, temperature, patient=mode == "online")
            last_used, last_note = "online", ""
            return msg
        except LLMError as exc:
            if mode == "online":
                raise
            why = str(exc)
    elif mode == "online":
        raise LLMError("no API key configured - add one in Settings -> AI")
    else:
        why = "no online AI key"
    local = _local() or _start_local()
    if local is None:
        raise LLMError((why + "; " if mode == "auto" else "") + "no local AI is running - "
                       "download the offline AI in Settings -> AI, or start Ollama")
    msg = _chat(local, messages, tools, tool_choice, temperature)
    last_used = "local"
    last_note = "" if mode == "local" else (
        ("Gemini limit reached" if "limit" in why else "No online AI" if "key" in why
         else "Gemini didn't answer") + " - using the offline AI")
    return msg


def _start_local() -> dict | None:
    try:
        from . import localai
        if localai.start():
            _local_seen["t"] = 0.0
            return _local()
    except ImportError:
        pass
    return None


def _chat(ai: dict, messages: list[dict], tools, tool_choice, temperature: float,
          patient: bool = True) -> dict:
    body: dict = {"model": ai["model"], "messages": messages,
                  "temperature": temperature}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(4):
        if ai["name"] == "online":
            _pace()
        request = urllib.request.Request(
            f"{ai['url']}/chat/completions", data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {ai['key']}"},
            method="POST")
        try:
            # a local AI without a graphics card is slow: give it longer
            with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT * (1 if ai["name"] == "online" else 6)) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            full = exc.read().decode("utf-8", "replace")
            detail = full[:400]
            if exc.code == 429:
                _learn_rpm(full)
                wait = _retry_after(full)
                if patient and wait is not None and wait <= RATE_WAIT_MAX_S and attempt < 3:
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
            raise LLMError(f"cannot reach the AI service at {ai['url']}: {exc}") from exc
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
