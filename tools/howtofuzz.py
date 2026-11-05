"""How do I...?  A storm of operator questions about using the app.

Random "help me align the lights", "where do i patch a par??", "im new how
do cues work" - every topic of the desk, asked many ways (templates,
lower case, typos, no question mark) - and checks that each one:

  * is taken as a QUESTION, not a request (and real requests aren't);
  * finds the guide section that answers it (top 2);
  * the no-AI copilot answers from the guide and changes nothing;
  * the AI copilot gets the guide on it (system text and how_to), and a
    turn that only looks it up leaves the rig, the show and undo as they were;
  * every button / menu the guide names in bold exists on a screen.

    python tools/howtofuzz.py                  600 questions, seed 1
    python tools/howtofuzz.py --turns 3000 --seed 7
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONSOLE_DATA_DIR", tempfile.mkdtemp(prefix="haze-howto-"))
os.environ.setdefault("CONSOLE_AUTOSAVE", "false")
sys.path.insert(0, str(ROOT))

from app import assistant, console_ai, howto  # noqa: E402

# (the guide section that must answer it - a regex on its title, what the operator wants to do)
TOPICS: list[tuple[str, list[str]]] = [
    (r"Lining up", ["align the lights", "line up my moving heads", "calibrate the movers to the room",
                    "make the real lights point where the 3d says", "line up the rig with the venue",
                    "get my movers aimed right in a new venue", "fix lights pointing at the wrong spot"]),
    (r"Fixture library", ["patch a light", "add a fixture", "add my chauvet spots", "find a light that isnt in the list",
                          "import a gdtf file", "change a light's dmx address", "make a fixture from the manual",
                          "add a light thats not in the library"]),
    (r"Quick buttons", ["make a button", "make a flash button", "add a page of buttons", "change a button colour",
                        "make a button that only changes colour", "edit a button", "arrange my buttons"]),
    (r"Cues and playbacks", ["record a cue", "make a cue list", "put cues on a fader", "update a cue",
                             "change the fade time of a cue", "go to the next cue", "record a cue on playback 2"]),
    (r"Groups", ["make a group", "add lights to a group", "rename a group", "remove a light from a group"]),
    (r"Palettes", ["make a colour palette", "record a position palette", "save a look as a palette",
                   "change a palette so every cue follows"]),
    (r"Effects", ["make a colour chase", "run an effect on the movers", "make a circle movement",
                  "stop an effect", "make a dimmer chase"]),
    (r"Timeline", ["use the timeline", "put a cue on the timeline", "make a show run on timecode"]),
    (r"light show per song|On the beat", ["make a show for a song", "sync the lights to the music", "find the drops in a song",
                                          "make the lights follow the beat", "tap the tempo"]),
    (r"Connecting a rig", ["connect to my artnet node", "send dmx to my lights", "go live", "use sacn",
                           "find my dmx node", "connect a usb dmx box"]),
    (r"The venue|Arrange", ["build my room", "add a truss", "move a truss", "change the room size", "add a stage"]),
    (r"room the lights know|Arrange", ["make a vip zone", "draw the dance floor", "keep lights out of the bar",
                               "make a keep out zone", "teach the room by pointing"]),
    (r"Lasers and special effects", ["fire the confetti", "set up the sparkle machine", "use the fog machine",
                                     "arm the effects", "turn the lasers on", "fire co2 on the drop", "set the spark height"]),
    (r"Safety|Strobe safety", ["stop everything at once", "blackout everything", "limit the strobes",
                               "keep strobes slow for epilepsy"]),
    (r"Shows", ["save my show", "open a show", "go back to an earlier version of my show"]),
    (r"templates", ["start a new show from a template", "load the demo show"]),
    (r"Workspaces", ["change the layout", "hide the 3d", "save my window layout"]),
    (r"Keyboard", ["see the keyboard shortcuts", "use the keyboard to go"]),
    (r"AI assistant|Talking|What the AI has learned", ["use the copilot", "set up the ai key", "talk to the copilot", "use voice control",
                               "see what the ai has learned", "make the ai forget something"]),
    (r"DJ requests from a phone", ["let the dj request colours from a phone", "turn on dj requests"]),
    (r"Hardware controllers|Stream Deck", ["use a midi controller", "connect my apc40", "set up a stream deck"]),
    (r"MVR", ["import an mvr plot", "export to vectorworks"]),
    (r"Philips Hue", ["make my hue lamps follow", "connect philips hue"]),
    (r"backup desk", ["set up a backup desk", "run two desks"]),
    (r"OSC", ["send osc to qlab", "trigger resolume"]),
    (r"Getting help", ["get help", "undo several changes", "see what a button does", "find a setting",
                       "get the desk to show me step by step"]),
    (r"Report a bug", ["report a bug", "send a problem report", "report a light that acts weird"]),
    (r"A newer Haze", ["update haze", "check for updates", "get the new version"]),
    (r"visualiser", ["move the 3d camera", "make the 3d look better", "see beams in the 3d"]),
    (r"Painting the rig", ["paint a gradient across the rig", "map a picture onto the lights", "make a shape movement"]),
    (r"Turned trusses", ["rotate a truss without moving the real lights"]),
    (r"Follow me speed", ["make the movers move slower when following"]),
    (r"Touch screens", ["use it on a touch screen"]),
    (r"Look and help", ["change the theme", "make the screen darker", "turn the tips off"]),
    (r"A video for the client", ["make a video for my client", "render the show to a video"]),
]
TEMPLATES = [
    "how do i {x}", "How do I {x}?", "help me {x}", "can you show me how to {x}", "where do i {x}",
    "how can i {x}", "whats the best way to {x}", "walk me through how to {x}", "is there a way to {x}?",
    "explain how to {x}", "im new, how do i {x}", "i dont know how to {x}", "how would i {x}?",
    "how to {x}", "teach me to {x}", "steps to {x}", "i'm stuck - how do i {x}", "guide me to {x}",
]
# requests, not questions: these must still go to the desk as looks
REQUESTS = ["make it blue", "help, it's too dark - brighter please", "strobe the floor", "warm wash on the vip",
            "align the movers to the dj", "line them up on the dance floor", "can you make it red", "fog please",
            "record this as a cue", "make a button for this look", "blackout", "where's the drop - hit it harder"]


def typo(rnd: random.Random, s: str) -> str:
    """An operator typing fast: a letter dropped or doubled, no capitals."""
    words = s.split()
    if len(words) > 2 and rnd.random() < 0.5:
        i = rnd.randrange(len(words))
        w = words[i]
        if len(w) > 4 and w.isalpha():
            j = rnd.randrange(1, len(w) - 1)
            words[i] = w[:j] + w[j + 1:] if rnd.random() < 0.5 else w[:j] + w[j] + w[j:]
    out = " ".join(words)
    return out.lower() if rnd.random() < 0.5 else out


def gui_names() -> tuple[list[str], list[str]]:
    """The **bold** names in the guide that look like a screen's words, and
    those no screen has (a renamed or removed button the guide still sends
    people to)."""
    guide = howto.GUIDE.read_text(encoding="utf-8")
    # the screens, and the names the engine gives them (templates, menus it sends)
    src = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                    for p in list((ROOT / "web").rglob("*.js")) + list((ROOT / "web").rglob("*.html"))
                    + list((ROOT / "app").glob("*.py")))
    src_l = re.sub(r"\s+", " ", src.lower().replace("\\u2026", "…").replace("&amp;", "&"))
    names = []
    for m in re.finditer(r"\*\*([^*\n]{2,48})\*\*", guide):
        raw = m.group(1)
        before = guide[max(0, m.start() - 40):m.start()]
        # a heading in bold ("**Mark the floor.**", "**Time limits:**") or emphasis
        # ("**armed and fired**") is not a button; a name is pressed, picked,
        # opened or reached through a menu (→)
        if raw.rstrip().endswith((".", ":")) or raw[:1].islower():
            continue
        if not re.search(r"(→|->|press|tap|click|pick|open|tick|choose|select|use|the|then|or|and|\(|,)\s*\**\s*$",
                         before, re.I):
            continue
        n = raw.strip()
        if len(n.split()) > 6 or re.search(r"[=`]|^\d|\bthe desk\b|^(not|never|only|one|all|every)\b", n, re.I):
            continue
        names.append(n)
    # every place a "Where:" line sends you to
    for line in re.findall(r"^\*Where:\*\s*(.+)$", guide, re.M):
        names += [re.sub(r"\s*\(.*?\)\s*", " ", x).strip() for x in line.split("→") if x.strip()]
    missing = []
    for n in sorted(set(names)):
        parts = [p.strip() for p in re.split(r"\s*(?:→|->|/)\s*", n) if p.strip()]
        ok = all(_on_screen(p, src_l) for p in parts)
        if not ok:
            missing.append(n)
    return sorted(set(names)), missing


def _on_screen(name: str, src_l: str) -> bool:
    n = re.sub(r"\s+", " ", name.lower()).replace("...", "…").strip(" …✓▶◂")
    n = re.sub(r"\s*\(.*?\)\s*", " ", n).strip()
    if not n or len(n) < 2:
        return True
    return n in src_l or n.replace("…", "") in src_l or n.replace(" ", "") in src_l.replace(" ", "")


def stand_in(question: str):
    """An AI that does what a good one should with a how-to question: looks it
    up, then answers from what it found - and calls nothing else."""
    seen = {"n": 0}

    def chat(msgs, tools=None, **_):
        seen["n"] += 1
        if seen["n"] == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "h1", "type": "function", "function": {"name": "how_to", "arguments": json.dumps({"question": question})}}]}
        got = json.loads(msgs[-1]["content"])
        return {"role": "assistant", "content": f"From the guide ({got.get('topic')}): " + (got.get("steps") or "")[:300]}
    return chat


def run(turns: int = 600, seed: int = 1, quiet: bool = False) -> dict:
    rnd = random.Random(seed)
    from app import engine as engine_mod
    eng = engine_mod.Engine(dry_run=True)
    eng.act("venue_template", name="club")
    eng.act("add_heads", query="moving head", qty=4)
    eng.act("add_heads", query="LED PAR", qty=4)
    eng.act("select_heads", heads=[1, 2]); eng.act("set_intensity", level=60); eng.act("record_cue", playback=1)
    eng.act("clear_programmer")
    before = json.dumps(eng.snapshot(), sort_keys=True, default=str)
    undo0 = len(eng._undo)
    probs: list[str] = []
    found = top1 = 0
    misses: dict[str, int] = {}
    asked = 0
    for i in range(turns):
        sect, tasks = rnd.choice(TOPICS)
        q = typo(rnd, rnd.choice(TEMPLATES).format(x=rnd.choice(tasks)))
        asked += 1
        if not howto.is_howto(q):
            probs.append(f"not taken as a question: {q!r}")
            continue
        hits = [s["title"] for s in howto.find(q, 2)]
        strange = howto.unknown_words(q)
        if strange:
            probs.append(f"the guide doesn't know {strange} in {q!r} (a word for the synonyms?)")
        if hits and re.search(sect, hits[0], re.I):
            top1 += 1
        if any(re.search(sect, t, re.I) for t in hits):
            found += 1
        else:
            misses[sect] = misses.get(sect, 0) + 1
            if misses[sect] <= 3:
                probs.append(f"{q!r}: wanted /{sect}/, the guide gave {hits}")
        if i % 7 == 0:
            r = console_ai.plan(q, offline=True, eng=eng)
            if r.get("steps"):
                probs.append(f"no-AI copilot acted on a question {q!r}: {r['steps']}")
            if not r.get("reply", "").startswith("From the guide") and hits:
                probs.append(f"no-AI copilot didn't answer from the guide: {q!r}")
        if i % 11 == 0:
            sys_text = assistant._system(eng, q)
            if hits and "THE GUIDE ON THIS QUESTION" not in sys_text:
                probs.append(f"the AI wasn't given the guide for {q!r}")
            out = assistant.run_turn(eng, q, session=f"h{i}", chat=stand_in(q))
            if not out.get("ok", True) or "error" in out and out.get("error"):
                probs.append(f"turn failed {q!r}: {out.get('error')}")
    for q in REQUESTS:
        if howto.is_howto(q):
            probs.append(f"a request taken as a question: {q!r}")
    after = json.dumps(eng.snapshot(), sort_keys=True, default=str)
    if len(eng._undo) != undo0:
        probs.append(f"questions left {len(eng._undo) - undo0} undo step(s)")
    if _no_time(before) != _no_time(after):
        probs.append("questions changed the desk")
    names, missing = gui_names()
    for n in missing:
        probs.append(f"the guide names **{n}** - no screen has it")
    res = {"asked": asked, "found_top2": found, "top1": top1, "misses": misses, "names": len(names),
           "missing_names": missing, "problems": probs}
    if not quiet:
        print(f"{asked} questions: the right guide section first {top1} ({100 * top1 // max(1, asked)}%), "
              f"in the top 2 {found} ({100 * found // max(1, asked)}%)")
        print(f"{len(names)} button / menu names in the guide, {len(missing)} not on any screen")
        for p in probs[:80]:
            print("  -", p)
        print("no problems" if not probs else f"{len(probs)} problem(s)")
    return res


def _no_time(s: str) -> str:
    """The desk's snapshot without what moves by itself (clocks, counters)."""
    d = json.loads(s)
    for k in ("t", "now", "time", "uptime", "fps", "frame", "rev", "act_rev", "clock", "beat", "phase", "ai"):
        d.pop(k, None)
    return json.dumps({k: v for k, v in d.items() if k in ("patch", "groups", "playbacks", "palettes", "quick", "venue",
                                                            "programmer", "selected")}, sort_keys=True, default=str)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    sys.exit(1 if run(a.turns, a.seed)["problems"] else 0)
