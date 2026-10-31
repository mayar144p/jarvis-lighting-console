"""The buttons to run a show live, built with it (the operator's own layout).

When the AI designs a show ("a Halloween show"), the cues and the timeline
are half of it.  The other half is what the operator plays live, so it is
built too, in a button profile of its own (named after the show):

    Default    each group's home look (aimed at its zone, its beam) and its
               movements to pick from - the show's starting point
    Show       per group: flash white, flash its colour, strobe, blackout;
               everything at once; a blackout per zone; GO; the effects
    Colours    a row of colours per group (the show's own first)
    Intensity  per group: its speed, its strobe and the strobe's speed
    Shoutout   the dance floor's movers on the VIP in white, a strobe,
               blackouts

with page buttons on every page to the others.  Built from the design and
the room (roomsense): the VIP's lights stay gentle, nothing strobes the bar.
"""
from __future__ import annotations

from . import autoshow, fixture_kind, roomsense

COLS = 12
PAGES = ("Default", "Show", "Colours", "Intensity", "Shoutout")
STANDARD = (("Red", "#ff0000"), ("Blue", "#0000ff"), ("Green", "#00ff00"), ("White", "#ffffff"),
            ("Magenta", "#ff00ff"), ("Pink", "#ff4fa0"), ("Amber", "#ffbf00"), ("Yellow", "#ffff00"),
            ("Orange", "#ff6a00"), ("Cyan", "#00ffff"), ("UV", "#5b2bff"))
MOVES_SHOW = (("circle", "Circle"), ("figure_eight", "Figure 8"), ("pan_sweep", "Sweep"), ("fan_pan", "Fan"))
COLOUR_FX = (("colour_chase", "Colour chase"), ("rainbow", "Rainbow"), ("breathe", "Breathe"),
             ("dimmer_chase", "Dimmer chase"), ("fan", "Fan colours"))
NAV_TINT = "#b8862b"           # the page buttons: one warm colour, like the operator's own


def _slot(row: int, col: int) -> int:
    return row * COLS + col + 1


def _sides(heads: list[int], by: dict) -> list[tuple[str, list[int]]]:
    """A group as its left and right halves (by where each light hangs), or
    whole when it is one light."""
    if len(heads) < 2:
        return [("", heads)]
    hs = sorted(heads, key=lambda n: float(by[n].get("x") or 0))
    mid = len(hs) // 2
    return [(" (L)", sorted(hs[:mid])), (" (R)", sorted(hs[mid:]))]


def _short(name: str, n: int = 18) -> str:
    return name.replace("Movers · ", "").replace("PARs · ", "PARs ").replace("Washes · ", "Washes ")[:n]


def calls(eng, d: dict, profile: str, playback: int | None = 1) -> list[dict]:
    """The kit as engine calls (one undo step with act_batch)."""
    a = autoshow.analyse(eng)
    groups = a["groups"]
    by = {h["head_no"]: h for h in eng.patch}
    room = roomsense.plan(eng)
    order = {"show": 0, "gentle": 1}
    base = sorted([k for k in groups if not k.endswith("@all")],
                  key=lambda k: (order.get(groups[k].get("duty", "show"), 2), not groups[k]["caps"]["pan_tilt"], k))
    movers = [k for k in base if groups[k]["caps"]["pan_tilt"]]
    show = [k for k in base if groups[k].get("duty", "show") == "show"]
    gentle_zones = [z for z in room["zones"] if z["duty"] == "gentle"]
    pal = [c for c in d.get("palette") or [] if isinstance(c, str)][:4]
    out: list[dict] = [{"action": "quick_profile", "params": {"new": profile}}]
    pages = {name: i + 1 for i, name in enumerate(PAGES)}
    if not gentle_zones:
        pages.pop("Shoutout")

    def btn(page, row, col, b):
        out.append({"action": "quick_set", "params": {"page": page, "slot": _slot(row, col), "button": b}})

    def nav(page, row):
        others = [n for n in pages if pages[n] != page]
        w = max(2, COLS // max(1, len(others)))
        for i, n in enumerate(others):
            btn(page, row, i * w, {"kind": "page", "label": f"{n} - page", "to_page": pages[n], "w": w,
                                   "tint": NAV_TINT, "mode": "tap"})

    def page_setup(name, rows):
        p = pages[name]
        out.append({"action": "quick_page", "params": {"page": p, "name": name}})
        out.append({"action": "quick_layout", "params": {"page": p, "cols": COLS, "rows": max(3, rows), "height": "fit"}})
        return p

    def zone_target(k):
        g = groups[k]
        t = f"zone_{autoshow._slug(g['where'])}"
        return a["targets"].get(t) or a["targets"].get("floor")

    # ---- Default: each group's home look and its movements -----------------
    rows = len(base) + 2
    p = page_setup("Default", rows)
    for r, k in enumerate(base[:10]):
        g = groups[k]
        heads = g["heads"]
        btn(p, r, 0, {"kind": "label", "label": _short(g["name"]), "sits": "side"})
        col = 1
        gentle = g.get("duty", "show") != "show"
        if g["caps"]["pan_tilt"]:
            x, y, z = zone_target(k)
            for side, hs in _sides(heads, by):
                out += [{"action": "clear_programmer", "params": {}},
                        {"action": "select_heads", "params": {"heads": hs}},
                        {"action": "set_intensity", "params": {"level": 60 if gentle else 100}},
                        {"action": "aim_at", "params": {"x": x, "y": y, "z": z}}]
                if g["caps"]["zoom"]:
                    out.append({"action": "set_attribute", "params": {"attribute": "zoom", "value": 215 if gentle else 60}})
                if g["caps"]["gobo"] and not gentle:
                    out.append({"action": "set_attribute", "params": {"attribute": "gobo", "value": 40}})
                out.append({"action": "quick_from_programmer", "params": {
                    "label": f"{_short(g['name'], 16)}{side}", "page": p, "slot": _slot(r, col), "heads": hs,
                    "on": False, "keep": ["position", "beam"]}})
                col += 1
            moves = [("circle", "Slow drift")] if gentle else [m for m in MOVES_SHOW if m[0] in g["fx"]][:3]
            for fx, label in moves:
                params = {"speed": 0.04, "size": 8} if gentle else {}
                btn(p, r, col, {"kind": "fx", "fx": fx, "label": label, "mode": "latch", "params": params,
                                "target": {"heads": heads}, "exclusive": f"move {r + 1}"})
                col += 1
            zone = g.get("zone")
            if not gentle and zone and zone not in room["no_roam"]:
                btn(p, r, col, {"kind": "fx", "fx": "roam", "label": "Roam", "mode": "latch",
                                "params": {"zones": [zone], "speed": 1.2, "size": 0.9},
                                "target": {"heads": heads}, "exclusive": f"move {r + 1}"})
                col += 1
        else:
            fx_here = [(fx, label) for fx, label in COLOUR_FX if fx in g["fx"]]
            if gentle:
                fx_here = [(fx, label) for fx, label in fx_here if fx == "breathe"]
            for fx, label in fx_here[:5]:
                params = {"colours": pal} if pal and fx in ("colour_chase", "rainbow", "fan") else {}
                btn(p, r, col, {"kind": "fx", "fx": fx, "label": label, "mode": "latch", "params": params,
                                "target": {"heads": heads}, "exclusive": f"fx {r + 1}"})
                col += 1
    out.append({"action": "clear_programmer", "params": {}})
    out.append({"action": "clear_selection", "params": {}})
    nav(p, rows - 1)

    # ---- Show: flash / strobe / blackout per group, all, zones, GO, SFX -----
    per_row = 2
    grows = (len(show) + per_row - 1) // per_row
    rows = grows + 4
    p = page_setup("Show", rows)
    for i, k in enumerate(show[:12]):
        g = groups[k]
        r, c0 = i // per_row, (i % per_row) * 6
        tgt = {"heads": g["heads"]}
        btn(p, r, c0, {"kind": "label", "label": _short(g["name"]), "sits": "side"})
        btn(p, r, c0 + 1, {"kind": "flash", "label": "Flash", "note": "white", "colour": "#ffffff", "target": tgt, "mode": "hold"})
        btn(p, r, c0 + 2, {"kind": "flash", "label": "Flash", "note": "same colour", "target": tgt, "mode": "hold"})
        btn(p, r, c0 + 3, {"kind": "strobe", "label": "Strobe", "note": "white", "colour": "#ffffff", "hz": 12,
                           "target": tgt, "mode": "hold"})
        btn(p, r, c0 + 4, {"kind": "kill", "label": "Blackout", "target": tgt, "mode": "hold", "tint": "#c0392b"})
    r = grows
    allt = {"heads": sorted({n for k in show for n in groups[k]["heads"]})} if show else {"all": True}
    btn(p, r, 0, {"kind": "label", "label": "All", "sits": "side"})
    btn(p, r, 1, {"kind": "flash", "label": "Flash all", "colour": "#ffffff", "target": allt, "mode": "hold"})
    btn(p, r, 2, {"kind": "strobe", "label": "Strobe all", "hz": 12, "colour": "#ffffff", "target": allt, "mode": "hold"})
    btn(p, r, 3, {"kind": "blackout", "label": "Blackout", "mode": "hold", "tint": "#c0392b"})
    if playback:                       # (buttons only: no cues to GO through)
        btn(p, r, 4, {"kind": "go", "label": f"GO {d.get('name', 'show')}"[:24], "playback": playback})
    col = 6
    for z in room["zones"]:
        zl = sorted(set(room["movers"].get(z["id"], [])) | set(room["statics"].get(z["id"], [])))
        if zl and col < COLS:
            btn(p, r, col, {"kind": "kill", "label": f"Blackout {z['name']}"[:24], "target": {"heads": zl},
                            "mode": "hold", "tint": "#c0392b"})
            col += 1
    sfx = [h for h in eng.patch if fixture_kind.describe(h).get("class") == "sfx"]
    col = 0
    for h in sfx[:COLS]:
        btn(p, r + 1, col, {"kind": "sfx", "label": (h.get("name") or h.get("model") or "Effect")[:24],
                            "mode": "tap", "seconds": 1, "target": {"heads": [h["head_no"]]}})
        col += 1
    nav(p, rows - 1)

    # ---- Colours: a row per group ------------------------------------------
    colourful = [k for k in base if groups[k]["caps"]["colour"]][:10]
    rows = len(colourful) + 2
    p = page_setup("Colours", rows)
    named = {hx.lower(): n for n, hx in STANDARD}
    cols = [(named.get(c.lower(), "Show " + str(i + 1)), c) for i, c in enumerate(pal)]
    cols += [(n, c) for n, c in STANDARD if c.lower() not in {x.lower() for _, x in cols}]
    for r, k in enumerate(colourful):
        g = groups[k]
        btn(p, r, 0, {"kind": "label", "label": _short(g["name"]), "sits": "side"})
        for i, (n, c) in enumerate(cols[:COLS - 1]):
            btn(p, r, i + 1, {"kind": "colour", "label": n, "colour": c, "mode": "latch",
                              "target": {"heads": g["heads"]}, "exclusive": f"colour {r + 1}"})
    nav(p, rows - 1)

    # ---- Intensity: speed, strobe, strobe speed per group -------------------
    per_row = 3
    rows = (len(base) + per_row - 1) // per_row + 2
    p = page_setup("Intensity", rows)
    for i, k in enumerate(base[:12]):
        g = groups[k]
        r, c0 = i // per_row, (i % per_row) * 4
        tgt = {"heads": g["heads"]}
        nm = _short(g["name"], 14)
        btn(p, r, c0, {"kind": "fader", "label": f"{nm} speed", "control": {"what": "lights", "mode": "speed"},
                       "default": 50, "target": tgt})
        btn(p, r, c0 + 1, {"kind": "strobe", "label": f"{nm} strobe", "mode": "hold", "hz": 10,
                           "follow_fader": True, "target": tgt})
        btn(p, r, c0 + 2, {"kind": "fader", "label": f"{nm} strobe speed", "control": {"what": "lights", "mode": "strobe"},
                           "default": 31, "target": tgt})
    nav(p, rows - 1)

    # ---- Shoutout: the dance floor's movers on the VIP ----------------------
    if "Shoutout" in pages:
        p = page_setup("Shoutout", 5)
        floor = sorted({n for k in movers if groups[k].get("duty", "show") == "show" for n in groups[k]["heads"]})
        btn(p, 1, 0, {"kind": "blackout", "label": "Blackout", "mode": "hold", "w": 2, "h": 2, "tint": "#c0392b"})
        btn(p, 1, 10, {"kind": "blackout", "label": "Blackout", "mode": "hold", "w": 2, "h": 2, "tint": "#c0392b"})
        for i, z in enumerate(gentle_zones[:2]):
            col = 3 + i * 4
            if floor:
                out += [{"action": "clear_programmer", "params": {}},
                        {"action": "select_heads", "params": {"heads": floor}},
                        {"action": "set_intensity", "params": {"level": 100}},
                        {"action": "set_colour", "params": {"hex": "#ffffff"}},
                        {"action": "aim_at", "params": {"x": z["centre"][0], "y": 0.0, "z": z["centre"][1]}},
                        {"action": "quick_from_programmer", "params": {
                            "label": f"{z['name']} shoutout"[:24], "page": p, "slot": _slot(1, col), "heads": floor,
                            "on": False}}]
            btn(p, 2, col, {"kind": "strobe", "label": "Strobe", "hz": 12, "mode": "hold", "w": 3,
                            "target": {"heads": floor} if floor else {"all": True}})
        out.append({"action": "clear_programmer", "params": {}})
        out.append({"action": "clear_selection", "params": {}})
        nav(p, 4)
    return out


def _free_name(eng, base: str) -> str:
    names = {n.lower() for n in eng._profiles()["order"]}
    base = str(base or "Show")[:20].strip() or "Show"
    name, i = base, 2
    while name.lower() in names:
        name, i = f"{base} {i}", i + 1
    return name


def _remember(eng, profile: str, d: dict, playback) -> None:
    """The show this profile plays, kept with the profiles (saved with the
    show): a later show can reuse its programming ("Christmas, from the
    Halloween one")."""
    p = eng._profiles()
    p.setdefault("shows", {})[profile] = {"design": d, "playback": playback}


def shows(eng) -> list[dict]:
    """The shows made so far: their profile, theme, colours, playback."""
    p = eng._profiles()
    out = []
    for name, s in (p.get("shows") or {}).items():
        if name in p["order"]:
            d = s.get("design") or {}
            out.append({"profile": name, "name": d.get("name"), "brief": d.get("brief", ""),
                        "palette": d.get("palette") or [], "playback": s.get("playback"),
                        "sections": [x["name"] for x in d.get("sections") or []]})
    return out


def build(eng, d: dict, playback: int | None = 1) -> dict:
    """Build the kit as one undo step, in a profile named after the show
    (playback None: buttons only - no cues to GO through)."""
    profile = _free_name(eng, d.get("name") or "Show")
    cs = calls(eng, d, profile, playback)
    res = eng.act_batch(cs, label="show buttons")
    if not res.get("ok"):
        raise ValueError(res.get("error") or "the show's buttons could not be made")
    _remember(eng, profile, d, playback)
    pages = [c["params"]["name"] for c in cs if c["action"] == "quick_page"]
    return {"profile": profile, "pages": pages,
            "summary": f"buttons for a live show in the “{profile}” profile: {', '.join(pages)} pages"}


def _swap(obj, cmap: dict):
    """Every colour in a button / design that is one of the old show's
    colours, changed to the new one in its place."""
    if isinstance(obj, str):
        return cmap.get(obj.lower(), obj)
    if isinstance(obj, list):
        return [_swap(x, cmap) for x in obj]
    if isinstance(obj, dict):
        return {k: _swap(v, cmap) for k, v in obj.items()}
    return obj


def restyle(eng, source: str, name: str, colours: list[str], cues: bool = True, buttons: bool = True) -> dict:
    """A new show with an earlier one's programming - the same looks,
    movements, effects, timing and buttons (the operator's own edits to
    them too) - in new colours (Halloween -> Christmas)."""
    p = eng._profiles()
    src = next((n for n in p.get("shows") or {} if n.lower() == str(source or "").strip().lower()), None)
    if src is None or src not in p["order"]:
        made = ", ".join(s["profile"] for s in shows(eng)) or "none yet"
        raise ValueError(f"no show called {source!r} to copy - the shows made so far: {made}")
    old = p["shows"][src]
    d0 = old["design"]
    pal_old = [c.lower() for c in d0.get("palette") or [] if isinstance(c, str)]
    pal_new = [c.lower() for c in colours or [] if isinstance(c, str) and autoshow.HEX.match(c)][:5]
    if not pal_new:
        raise ValueError("give the new show's colours (2-5, #rrggbb)")
    cmap = {c: pal_new[i % len(pal_new)] for i, c in enumerate(pal_old)}
    d = _swap(d0, cmap)
    d = {**d, "name": str(name or "Show")[:40], "palette": pal_new,
         "brief": f"{d0.get('brief', '')} - the {src} show's programming in new colours"[:300]}
    out = {"name": d["name"]}
    pb = None
    if cues:
        pb = next((pl["n"] for pl in eng.playbacks if not pl.get("stack")), None) or old.get("playback") or 1
        built = autoshow.build(eng, d, pb)
        out["cues"] = built["summary"]
    if buttons:
        profile = _free_name(eng, d["name"])
        with eng.lock:
            if p["active"] != src:
                eng.act("quick_profile_use", name=src)
            res = eng.act("quick_profile", new=profile, copy=True)        # (switches to the copy)
            if not res.get("ok"):
                raise ValueError(res.get("error") or "the buttons could not be copied")
            go_pb = pb or old.get("playback")
            for i, b in enumerate(eng.quick):
                nb = _swap(b, cmap)
                if nb.get("kind") == "colour" and nb.get("colour") != b.get("colour"):
                    # (a colour button says its new colour)
                    nb["label"] = {hx.lower(): n for n, hx in STANDARD}.get(str(nb["colour"]).lower(), "Show colour")
                if nb.get("kind") == "go" and go_pb:
                    nb["playback"] = go_pb
                    nb["label"] = f"GO {d['name']}"[:24]
                eng.quick[i] = nb
            _remember(eng, profile, d, pb or old.get("playback"))
        out["profile"] = profile
    out["summary"] = (f"“{d['name']}”: the {src} show's programming in new colours"
                      + (f" - cues on playback {pb}" if pb else "")
                      + (f", buttons in the “{out['profile']}” profile" if buttons else ""))
    return out
