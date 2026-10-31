"""What each part of the room is FOR, and which lights serve it.

A lighting designer walks into a club and knows, before touching a fader:
the dance floor is where the show happens; the VIP area wants something
gentle and flattering, not beams in people's drinks; the DJ must never get a
beam or a strobe in the eyes; the bar staff are working and a moving light
sweeping the bar all night drives them mad; the stage is lit for the people
on it, not swept with roaming beams; and a keep-out is never touched.

This module turns the venue's zones and the patch into that knowledge:

    plan(eng)    every zone with its duty and what it may get; the moving
                 lights shared out between the zones that want a show (two
                 on the dance floor, one on the VIP... by importance and by
                 which zone each light is nearest); the static lights by the
                 zone they light; and the rules, in words, for the AI.
    NO_ROAM      the kinds of zone a roaming beam never wanders into.

The show builder (autoshow.py), the AI (assistant.py) and roam
(engine_roam.py) all use it, so the same rules hold whichever made the look.
"""
from __future__ import annotations

import math

from . import fixture_kind
from . import venue as venue_mod

# duty, how much of the moving light it gets, what it may have, in words
ZONE_DUTY = {
    "dancefloor": ("show", 3.0, "the show: movement, colour, effects and hits on the drops"),
    "standing": ("show", 2.0, "the crowd: movement and colour, hits on the drops"),
    "vip": ("gentle", 1.0, "gentle and flattering: warm or soft colour, slow or no movement, "
                           "no strobes, no beams sweeping through people's faces"),
    "seating": ("gentle", 0.8, "people sitting: soft, slow, low; no strobes"),
    "foh": ("work", 0.0, "the operator's position: no beams, no strobes"),
    "stage": ("performer", 0.0, "the people on it: light them from the front, warm and steady; "
                                "never roam or strobe into their eyes"),
    "dj": ("protect", 0.0, "the DJ: backlight or a soft wash at most; never a beam or a strobe "
                           "in their face, and no roaming over the decks"),
    "bar": ("staff", 0.0, "the bar staff are working: at most a soft static wash; no moving "
                          "beams, no roaming, no strobes"),
    "backstage": ("never", 0.0, "out of the show: leave it dark"),
    "keepout": ("never", 0.0, "never light it"),
}
# a roaming beam never wanders into these (the DJ's eyes, the bar staff, the
# performers, the operator, out of the show)
NO_ROAM = ("dj", "bar", "stage", "foh", "backstage", "keepout")
# and no strobe or flash hits are aimed into these
NO_HITS = ("vip", "seating", "dj", "bar", "stage", "foh", "backstage", "keepout")


def _area(pts) -> float:
    s = 0.0
    for i in range(len(pts)):
        x1, z1 = pts[i]
        x2, z2 = pts[(i + 1) % len(pts)]
        s += x1 * z2 - x2 * z1
    return abs(s) / 2


def _moves(h: dict) -> bool:
    m = set(h.get("map") or [])
    return "pan" in m and "tilt" in m


def zones(v: dict) -> list[dict]:
    out = []
    for z in (venue_mod.normalise(v) if v else {}).get("zones") or []:
        if not z.get("points"):
            continue
        duty, weight, says = ZONE_DUTY.get(z["kind"], ("show", 1.0, "part of the room"))
        cx, cz = venue_mod.zone_centroid(z)
        out.append({"id": z["id"], "name": z.get("name") or z["kind"], "kind": z["kind"], "duty": duty,
                    "weight": weight, "says": says, "centre": [round(cx, 2), round(cz, 2)],
                    "area": round(_area(z["points"]), 1)})
    return out


def share(n: int, wanted: list[tuple[str, float]]) -> dict[str, int]:
    """n lights between zones by weight (largest remainder); with plenty of
    lights (two a zone) every zone gets at least one."""
    wanted = [(k, w) for k, w in wanted if w > 0]
    if not n or not wanted:
        return {}
    total = sum(w for _, w in wanted)
    base = {k: 0 for k, _ in wanted}
    if n >= 2 * len(wanted):
        for k in base:
            base[k] = 1
        left = n - len(wanted)
    else:
        left = n
    raw = {k: left * w / total for k, w in wanted}
    for k in raw:
        base[k] += int(raw[k])
    rest = n - sum(base.values())
    for k, _ in sorted(raw.items(), key=lambda kv: (-(kv[1] - int(kv[1])), -dict(wanted)[kv[0]])):
        if rest <= 0:
            break
        base[k] += 1
        rest -= 1
    return {k: v for k, v in base.items() if v}


def plan(eng) -> dict:
    """The room's duties and which lights serve which zone (see the top)."""
    zs = zones(eng.venue or {})
    show = [z for z in zs if z["weight"] > 0]
    movers = [h for h in eng.patch if _moves(h) and fixture_kind.describe(h).get("class", "light") == "light"]
    statics = [h for h in eng.patch if not _moves(h) and fixture_kind.describe(h).get("class", "light") == "light"]
    by_zone: dict[str, list[int]] = {}
    if show and movers:
        quota = share(len(movers), [(z["id"], z["weight"] * max(1.0, math.sqrt(z["area"] or 1))) for z in show])
        zone = {z["id"]: z for z in show}
        # nearest light to each zone first, so a zone gets the lights that see it best
        pairs = sorted(((math.hypot(float(h.get("x") or 0) - zone[k]["centre"][0],
                                    float(h.get("z") or 0) - zone[k]["centre"][1]), h["head_no"], k)
                        for h in movers for k in quota), key=lambda t: t[0])
        taken: set[int] = set()
        for _d, n, k in pairs:
            if n in taken or len(by_zone.get(k, [])) >= quota[k]:
                continue
            by_zone.setdefault(k, []).append(n)
            taken.add(n)
        for k in by_zone:
            by_zone[k].sort()
    # a static light right by the VIP, the seating or the bar lights that
    # area (it takes on its job); one by the DJ or the stage is usually the
    # show's own uplight or stage wash, so it stays in the show
    lit: dict[str, list[int]] = {}
    near = [z for z in zs if z["kind"] in ("vip", "seating", "bar")]
    for h in statics:
        x, z = float(h.get("x") or 0), float(h.get("z") or 0)
        best = min(near, key=lambda q: math.hypot(x - q["centre"][0], z - q["centre"][1]), default=None)
        if best and math.hypot(x - best["centre"][0], z - best["centre"][1]) <= max(3.0, math.sqrt(best["area"] or 1)):
            lit.setdefault(best["id"], []).append(h["head_no"])
    name = {z["id"]: z["name"] for z in zs}
    rules = [f"- {z['name']} ({z['kind']}): {z['says']}." for z in zs]
    if by_zone:
        rules.append("Moving lights by zone (use these, don't send them elsewhere without a reason): "
                     + "; ".join(f"{name[k]}: lights {', '.join(map(str, v))}" for k, v in by_zone.items()) + ".")
    if lit:
        rules.append("Static lights near each zone: "
                     + "; ".join(f"{name[k]}: lights {', '.join(map(str, v))}" for k, v in lit.items()) + ".")
    no_roam = sorted({z["name"] for z in zs if z["kind"] in NO_ROAM})
    if no_roam:
        rules.append("Never roam, sweep or strobe over: " + ", ".join(no_roam) + ".")
    return {"zones": zs, "movers": by_zone, "statics": lit, "rules": rules,
            "no_roam": [z["id"] for z in zs if z["kind"] in NO_ROAM],
            "no_hits": [z["id"] for z in zs if z["kind"] in NO_HITS]}


def describe(p: dict) -> str:
    if not p["zones"]:
        return ("The room has no zones yet: draw the dance floor, VIP, bar, DJ and stage in the Venue tab "
                "(or ask me to add them) so the show knows where the people and the staff are.")
    return "THE ROOM (what each zone is for):\n" + "\n".join(p["rules"])


# Lighting design knowledge the AI works from (with the room's own rules)
DESIGN_KNOWLEDGE = """LIGHTING DESIGN - what looks good and what doesn't:
* Each zone has a job (THE ROOM below). Share the moving lights out by it: most on the
  dance floor, one or two gentle ones on the VIP; nothing roaming over the DJ, the bar
  or the stage. A small rig: 2 on the floor, 1 on the VIP is better than 3 doing the same.
* Colour: 2-3 colours per look, one leading. Complementary pairs work (blue/amber,
  magenta/green-cyan, red/white); a rainbow on everything looks cheap. Warm (amber,
  warm white, soft pink) flatters faces: VIP, speeches, the stage. Saturated deep
  colours (blue, magenta, red, UV) suit the dance floor. White is punctuation: hits, drops.
* Contrast makes the show: not everything on at once. Calm parts dim, slow, few lights;
  the drop full, fast, everything. Leave some lights dark so the lit ones read.
* Movement: slow and wide for calm parts, faster on the beat for peaks; mirror left
  and right; beams aim at the floor or above heads, never at eye level into the crowd,
  the DJ or the bar.
* Strobes and flashes: short, only on the drops / the last bars of a build, only over
  the dance floor; never the VIP, the bar, the DJ or the stage; never for speeches.
* Speeches, first dance, cake: warm and steady, no movement or effects, the person lit.
* Beams (narrow, gobos) need haze to show; without it, use colour washes.
* Use what each light can do (WHAT EACH LIGHT CAN DO): PARs and bars wash and chase,
  spots do gobos and aims, beams cut the air, washes colour the room.
"""
