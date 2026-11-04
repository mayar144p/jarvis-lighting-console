"""How do I...?  Answers about the desk itself, from its own guide.

"Help me align the lights", "where do I patch a light?", "how do I make a
button" are questions about the app, not requests to change the rig.  The
copilot answers them from docs/GUIDE.md - the real button names and steps -
instead of from what a model remembers of other desks, and changes nothing.

    is_howto(text)  -> is it a question about how to use the desk?
    find(text, n)   -> the guide's best sections for it, best first
    answer(text)    -> {"topic", "steps", "also", "found"} for the copilot
"""
from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "GUIDE.md"

# "how do I...", "help me...", "where is...", "what does X do" - a question
# about the desk.  ("make it blue" or "help, it's too dark - brighter" are not.)
_HOWTO = re.compile(
    r"^\s*(how\s+(do|can|would|should|to)\b|how\s+does\b|how\s+is\b|where\s+(do|can|is|are)\b"
    r"|what\s+(does|do|is)\s+(the\s+)?[\w\s'-]{1,40}\s+(do|for|mean)\b|what('?s|\s+is)\s+the\s+(way|best\s+way)\b"
    r"|can\s+(i|you\s+show\s+me|you\s+explain|you\s+teach)\b|is\s+there\s+(a|an|any)\s+(way|button|setting|option)\b"
    r"|help\s+me\s+(?!out\b)\w+"
    r"|show\s+me\s+how\b|teach\s+me\b|explain\b|walk\s+me\s+through\b|i\s+(don'?t|do\s+not)\s+know\s+how\b"
    r"|i\s+want\s+to\s+learn\b|i('?m|\s+am)\s+(new|lost|stuck)\b|tutorial\b|guide\s+me\b|steps\s+to\b)", re.I)

# operator words -> the guide's words (whole words, lower case)
SYNONYMS: dict[str, str] = {
    "align": "line up lining mark marks", "alignment": "line up lining mark", "calibrate": "line up lining",
    "calibration": "line up lining", "line": "line up lining", "lineup": "line up lining", "focus": "line up aim",
    "aim": "aim aiming position point follow", "point": "aim position", "patch": "add fixture library patch address",
    "patching": "add fixture library patch address", "address": "address dmx patch", "fixture": "fixture library add",
    "fixtures": "fixture library add", "light": "fixture", "lights": "fixture", "add": "add fixture",
    "profile": "fixture library editor", "personality": "fixture library", "gdtf": "gdtf fixture library",
    "button": "quick buttons", "buttons": "quick buttons", "flash": "quick buttons flash", "page": "quick buttons pages",
    "pages": "quick buttons pages", "cue": "cues record playback", "cues": "cues record playback", "record": "record cue",
    "playback": "playback cues faders", "fader": "faders playback", "faders": "faders playback",
    "chase": "effects chase step", "effect": "effects", "fx": "effects", "strobe": "strobe safety",
    "timeline": "timeline", "song": "song per", "songs": "song per", "music": "beat song sound", "beat": "beat bpm tempo",
    "bpm": "beat tempo", "tempo": "beat tempo tap", "sync": "beat link dj", "cdj": "pro dj link", "pioneer": "pro dj link",
    "ableton": "ableton link", "midi": "midi hardware controllers", "controller": "hardware controllers",
    "streamdeck": "stream deck", "apc40": "midi hardware controllers", "apc": "midi hardware controllers",
    "akai": "midi hardware controllers", "launchpad": "midi hardware controllers", "camera": "visualiser camera",
    "darker": "look themes dark", "brighter": "look themes", "following": "follow speed", "slower": "speed",
    "updates": "newer haze update", "version": "newer haze update", "remove": "take out", "osc": "osc", "qlab": "osc", "resolume": "osc",
    "artnet": "art-net connecting rig", "art-net": "art-net connecting rig", "sacn": "sacn connecting rig",
    "dmx": "dmx connecting rig output", "node": "art-net connecting rig node", "output": "connecting rig live output",
    "live": "live output go", "connect": "connecting rig", "usb": "usb dmx",
    "room": "venue room", "venue": "venue", "stage": "venue stage", "truss": "truss arrange venue",
    "trusses": "truss arrange", "zone": "zones room teach", "zones": "zones room teach", "vip": "zones",
    "keepout": "keep-out zones", "3d": "visualiser", "visualizer": "visualiser", "visualiser": "visualiser",
    "colour": "colour palettes", "color": "colour palettes", "colours": "colour palettes", "colors": "colour palettes",
    "palette": "palettes", "palettes": "palettes", "group": "groups", "groups": "groups",
    "move": "movement move", "movement": "move movement", "roam": "roam room teach", "shape": "shapes painting",
    "gradient": "painting gradient", "pixel": "pixels painting", "video": "video client", "picture": "painting pictures",
    "laser": "lasers special effects", "lasers": "lasers special effects", "fog": "fog haze special effects",
    "haze": "fog haze special effects", "smoke": "fog haze", "confetti": "confetti special effects",
    "co2": "co2 special effects", "sparks": "sparks special effects", "sparkle": "sparks special effects",
    "spark": "sparks special effects", "flame": "flames special effects", "arm": "arm special effects",
    "save": "shows save", "open": "shows open", "load": "shows open", "show": "shows", "backup": "backup desk",
    "template": "templates demo", "templates": "templates demo", "demo": "templates demo",
    "layout": "workspaces", "workspace": "workspaces", "window": "workspaces", "windows": "workspaces",
    "keyboard": "keyboard", "shortcut": "keyboard", "shortcuts": "keyboard", "hotkey": "keyboard",
    "touch": "touch screens", "tablet": "touch screens", "command": "command bar", "type": "command bar",
    "ai": "copilot assistant", "copilot": "copilot assistant", "assistant": "copilot assistant",
    "key": "copilot assistant key", "gemini": "copilot assistant", "voice": "voice talk copilot",
    "talk": "voice talk copilot", "hue": "philips hue", "philips": "philips hue", "mvr": "mvr plots",
    "vectorworks": "mvr plots", "capture": "mvr plots", "theme": "look themes", "dark": "look themes dark",
    "help": "look help", "tour": "look help tour", "safety": "safety", "blackout": "safety blackout",
    "kill": "safety kill", "emergency": "safety blackout", "invert": "invert hang", "hang": "hang hung",
    "upside": "invert hang", "flip": "invert hang", "inverted": "invert hang", "mirror": "flip mirror",
    "speed": "follow speed", "follow": "follow speed", "gobo": "gobos", "gobos": "gobos",
    "dj": "dj requests booth", "request": "dj requests", "requests": "dj requests", "phone": "dj requests",
    "update": "update newer", "bug": "report bug", "report": "report bug", "check": "venue check-in ready",
    "ready": "ready check", "undo": "undo", "mode": "modes set up program live", "modes": "set up program live",
    "rotate": "turned trusses", "rotated": "turned trusses", "turn": "turned trusses",
}
_STOP = set("a an the i me my you your to do does how can could would should is are it in on of for with "
            "and or what where why when which this that these those please help want need make get use "
            "some any there here best way walk through teach guide explain steps stuck new dont don't know "
            "learn im i'm tutorial lost".split())
# the asking part of a question ("can you show me how to", "is there a way
# to") - its words ("show", "way") are not what the question is about
_ASKING = re.compile(r"^\s*(can\s+you\s+)?(show|tell|teach|explain|walk)\s+me\s+(how\s+)?(to\s+|through\s+)?"
                     r"|^\s*(is\s+there\s+a\s+way|what'?s\s+the\s+(best\s+)?way|steps|guide\s+me)\s+(to\s+)?", re.I)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9'-]*", str(text or "").lower())


@lru_cache(maxsize=1)
def sections() -> tuple[dict, ...]:
    """The guide cut at its headings: [{title, level, body, words}]."""
    try:
        text = GUIDE.read_text(encoding="utf-8")
    except OSError:
        return ()
    out: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = re.match(r"^(#{2,4})\s+(.*)", line)
        if m:
            cur = {"title": m.group(2).strip(), "level": len(m.group(1)), "lines": []}
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    for s in out:
        s["body"] = "\n".join(s.pop("lines")).strip()
        s["title_words"] = set(_words(s["title"]))
        s["words"] = _words(s["body"])
    return tuple(s for s in out if s["body"])


# the words a how-to question starts with: a typo in one ("stepps to",
# "wheere do i", "is thhere a way") is still that word
_LEADS = ("how", "where", "what", "whats", "what's", "steps", "guide", "teach", "explain", "walk", "help",
          "show", "is", "there", "can", "tutorial", "learn")


def _fix_leads(text: str) -> str:
    words = text.split()
    for i, w in enumerate(words[:3]):
        lw = w.lower()
        if len(lw) >= 4 and lw not in _LEADS:
            near = difflib.get_close_matches(lw, _LEADS, n=1, cutoff=0.8)
            if near:
                words[i] = near[0]
    return " ".join(words)


def is_howto(text: str) -> bool:
    """A question about how to use the desk (not a look to make)."""
    t = _fix_leads(str(text or "").strip())
    # "how do I..." later in the sentence ("I'm stuck - how do I...") asks the same
    if re.search(r"\bhow\s+(do|can|would|should)\s+(i|we)\b|\bhow\s+to\s+\w+", t, re.I) and len(t.split()) <= 30:
        return True
    return bool(t) and (bool(_HOWTO.search(t)) or (t.endswith("?") and bool(re.match(
        r"^\s*(how|where|which\s+(button|menu|tab|window))\b", t, re.I))))


# what an operator calls a thing -> the guide's words for it (before single words)
PHRASES: dict[str, str] = {
    "go live": "go live art-net output connecting rig", "next cue": "go cue playback", "fade time": "fade cue",
    "dmx address": "address patch fixture library", "dance floor": "dance floor zone", "keep out": "keep-out zone",
    "voice control": "voice talk copilot", "keyboard shortcuts": "keyboard", "stream deck": "stream deck",
    "check for updates": "newer haze update", "room size": "venue room size arrange",
    "screen darker": "look themes dark show dark", "slower when following": "follow speed slow",
    "when following": "follow speed", "3d camera": "visualiser camera view", "new version": "newer haze update", "two desks": "backup desk",
    "timecode": "timeline timecode ltc", "wrong spot": "line up lining mark", "window layout": "workspaces",
    "hide the 3d": "workspaces no 3d", "earlier version": "shows versions", "acts weird": "report problem light",
    "problem report": "report bug", "tips off": "look help tips", "circle movement": "effects movement circle",
    "colour chase": "effects chase", "color chase": "effects chase", "dimmer chase": "effects chase dimmer",
    "stop everything": "safety blackout kill", "at once": "kill blackout", "fire co2": "co2 special effects",
}


@lru_cache(maxsize=1)
def _vocab() -> tuple[str, ...]:
    """Every word the guide and the synonyms know (to put a typo right)."""
    v = set(SYNONYMS)
    for s in sections():
        v.update(w for w in s["words"] if len(w) >= 4)
    return tuple(sorted(v))


def _spell(w: str) -> str:
    if len(w) < 4 or w in SYNONYMS or w in _STOP:
        return w
    voc = _vocab()
    if w in voc:
        return w
    near = difflib.get_close_matches(w, voc, n=1, cutoff=0.82 if len(w) >= 5 else 0.85)
    return near[0] if near else w


def _terms(text: str) -> list[str]:
    text = _ASKING.sub("", _fix_leads(str(text or "")))
    raw = " " + " ".join(_words(text)) + " "
    low = " " + " ".join(_spell(w) for w in _words(text)) + " "
    extra: list[str] = []
    for p, more in PHRASES.items():
        if f" {p} " in low or f" {p} " in raw:
            extra += more.split()
    words = [w for w in low.split() if w not in _STOP] + extra
    out = list(words)
    for w in list(words):
        out += SYNONYMS.get(w, "").split()
        if w.endswith("s") and w[:-1] in SYNONYMS:
            out += SYNONYMS[w[:-1]].split()
    return out


def find(text: str, n: int = 3) -> list[dict]:
    """The guide's sections that answer `text`, best first."""
    terms = _terms(text)
    if not terms:
        return []
    scored = []
    for s in sections():
        body = s["words"]
        bag: dict[str, int] = {}
        for w in body:
            bag[w] = bag.get(w, 0) + 1
        score = 0.0
        for t in set(terms):
            if t in s["title_words"]:
                score += 6
            c = bag.get(t, 0)
            if c:
                score += min(4.0, 1.0 + c / 3)
        # the words of the question side by side ("line up", "quick buttons")
        joined = " ".join(body)
        for a, b in zip(terms, terms[1:]):
            if f"{a} {b}" in joined:
                score += 2
        if score > 0:
            scored.append((score / (1 + len(body) / 1500), s))
    scored.sort(key=lambda x: -x[0])
    return [s for _sc, s in scored[:n]]


def reflow(body: str) -> str:
    """The guide's hard-wrapped lines joined back into paragraphs and list
    items (a screen wraps them itself)."""
    out: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            if out and out[-1] != "":
                out.append("")
        elif not out or out[-1] == "" or re.match(r"^(\d+\.|[-*•]|\|)\s", s) or s.startswith("```"):
            out.append(s)
        else:
            out[-1] += " " + s
    return "\n".join(out).strip()


def answer(text: str, limit: int = 3500) -> dict:
    """What the copilot reads to answer a how-to question."""
    hits = find(text, 3)
    if not hits:
        return {"found": False, "topic": "", "steps": "",
                "also": [s["title"] for s in sections() if s["level"] == 2][:12],
                "note": "the guide has nothing on this - say so plainly and offer the nearest topics; never "
                        "invent a button or a menu"}
    best = hits[0]
    body = reflow(best["body"])
    if len(body) > limit:
        body = body[:limit].rsplit("\n", 1)[0] + "\n..."
    return {"found": True, "topic": best["title"], "steps": body,
            "also": [s["title"] for s in hits[1:]]}


def reply(text: str) -> str:
    """A plain answer with no AI at all: the guide's own steps."""
    a = answer(text, 3500)
    if not a["found"]:
        return ("I couldn't find that in the guide. Topics it covers: " + ", ".join(a["also"][:8])
                + ". Help (?) in the top bar has the whole guide.")
    more = f"\n\nSee also: {', '.join(a['also'])}." if a["also"] else ""
    return f"From the guide - {a['topic']}:\n\n{a['steps']}{more}"
