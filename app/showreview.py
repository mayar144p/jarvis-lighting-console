"""The show judged before doors: the design, not just the wiring.

The ready check (engine_shows._a_ready_check) asks "will it work?".  This
asks "is it right?", with the room's rules (roomsense):

  * a cue that puts a moving light's beam on the DJ, the bar, the stage or a
    keep-out;
  * a strobe / flash button on the VIP's, the bar's or the DJ's own lights;
  * a roam over the DJ / bar / stage (the desk refuses it when pressed);
  * a button aimed at lights or a group that are gone, a GO with no cues
    behind it;
  * a cue with every light dark (not a deliberate blackout cue);
  * effects armed before doors.

Each item: level (bad / warn / info), the text, and when it can be put right
in one tap `act` ({action, params}) - or `do` for the screen to open.
"""
from __future__ import annotations

from . import roomsense
from . import venue as venue_mod

MAX_CUES = 64


def _lands(eng, h: dict, vals: dict) -> tuple[float, float] | None:
    """Where this light's beam meets the floor for these values (x, z)."""
    try:
        pdeg, tdeg = eng._head_deg(h, {"pan": vals["pan"], "tilt": vals["tilt"]})
        return eng._beam_hit(h, pdeg, tdeg)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def items(eng) -> list[dict]:
    out: list[dict] = []

    def add(level, text, act=None, do=""):
        out.append({"level": level, "text": text, **({"act": act} if act else {}), **({"do": do} if do else {})})

    plan = roomsense.plan(eng)
    by = {h["head_no"]: h for h in eng.patch}
    zones = {z["id"]: z for z in plan["zones"]}
    shy = [z for z in plan["zones"] if z["kind"] in roomsense.NO_ROAM]
    gentle_heads = {n for z in plan["zones"] if z["duty"] != "show"
                    for n in plan["movers"].get(z["id"], []) + plan["statics"].get(z["id"], [])}
    # -- cues: beams on the DJ / bar / stage, and all-dark cues ---------------
    from .engine_roam import _inside
    pts = {zz["id"]: zz["points"] for zz in venue_mod.normalise(eng.venue).get("zones") or [] if zz.get("points")}
    seen = 0
    for pb in eng.playbacks:
        for c in pb.get("stack") or []:
            seen += 1
            if seen > MAX_CUES:
                break
            vals = c.get("values") or {}
            where = f"PB{pb['n']} cue {c['n']}" + (f" ({c['name']})" if c.get("name") else "")
            if vals and all(float((r or {}).get("dimmer", 1) or 0) == 0 for r in vals.values()) \
                    and "black" not in str(c.get("name") or "").lower():
                add("warn", f"{where}: every light in it is dark.", do="cue_list")
            hit: dict[str, list[int]] = {}
            for n, r in vals.items():
                h = by.get(int(n))
                if not h or "pan" not in r or "tilt" not in r or not float(r.get("dimmer", 100) or 0):
                    continue
                spot = _lands(eng, h, r)
                if spot is None:
                    continue
                for z in shy:
                    if z["id"] in pts and _inside(pts[z["id"]], spot[0], spot[1]):
                        hit.setdefault(z["name"], []).append(h["head_no"])
            for zname, heads in hit.items():
                add("bad", f"{where} puts the beam of light{'s' if len(heads) > 1 else ''} "
                           f"{', '.join(map(str, heads))} on the {zname}.", do="cue_list")
    # -- buttons ---------------------------------------------------------------
    groups = {g["n"] for g in eng.groups}
    stacks = {pb["n"]: len(pb.get("stack") or []) for pb in eng.playbacks}
    for b in eng.quick:
        t = b.get("target") or {}
        name = f"Button “{b['label']}” (page {b['page']})"
        clear = {"action": "quick_set", "params": {"page": b["page"], "slot": b["slot"], "clear": True}}
        if t.get("heads") and not any(n in by for n in t["heads"]):
            add("bad", f"{name} works on lights that are no longer patched.", act=clear)
            continue
        if t.get("group") is not None and int(t["group"]) not in groups:
            add("bad", f"{name} works on a group that is gone.", act=clear)
            continue
        if b["kind"] == "go" and not stacks.get(int(b.get("playback") or 1)):
            add("warn", f"{name}: GO on playback {b.get('playback') or 1}, which has no cues.", act=clear)
        if b["kind"] in ("strobe", "flash") and t.get("heads") and set(t["heads"]) & gentle_heads \
                and set(t["heads"]) <= gentle_heads:
            add("warn", f"{name} strobes / flashes the VIP's, the bar's or the DJ's own lights.")
        if b["kind"] == "fx" and b.get("fx") == "roam":
            zs = (b.get("params") or {}).get("zones") or []
            bad = [zones[z]["name"] for z in zs if z in zones and zones[z]["kind"] in roomsense.NO_ROAM]
            if bad:
                add("bad", f"{name} roams over the {', '.join(bad)} - the desk won't (the DJ's eyes, the bar staff).",
                    act=clear)
    # -- effects ---------------------------------------------------------------
    if float(getattr(eng, "fx_armed_until", 0) or 0) > 0:
        add("warn", "Special effects and lasers are ARMED before doors - disarm until the moment.",
            act={"action": "fx_arm", "params": {"state": False}})
    return out
