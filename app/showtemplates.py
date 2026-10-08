"""Show templates (backlog A10 item 2): a new show that starts 80 % done.

Each template is a room (app/venue.py's templates), real lights from the
bundled library hung where that kind of show hangs them, groups, colour
and position palettes, a page of buttons and a first set of cues (the
offline show designer).  Built by the engine's own actions, as ONE undo
step (see Engine._a_show_template).

The demo show is the club night, playing.
"""
from __future__ import annotations

# (group name, library source, key, how many, rig id in that room or None
# for "where that kind of light goes", where on the rig)
TEMPLATES: dict[str, dict] = {
    "club": {
        "label": "Club night", "venue": "club",
        "blurb": "Beams and washes on the trusses, PARs over the floor, strobes. Busking buttons and a night of cues.",
        "lights": [
            ("Beams", "qlc", "Clay_Paky/Clay-Paky-Sharpy-Plus.qxf", 4, "r1", "spread"),
            ("Washes", "qlc", "Chauvet/Chauvet-Intimidator-Wash-Zoom-350-IRC.qxf", 4, "r2", "spread"),
            ("PARs", "qlc", "American_DJ/American-DJ-Mega-PAR-Profile-Plus.qxf", 6, "r3", "spread"),
            ("Strobes", "qlc", "Chauvet/Chauvet-Shocker-90-IRC-QRG.qxf", 2, "r2", "both ends"),
        ],
        "palettes": [("Red", "#ff0000"), ("Blue", "#0033ff"), ("Magenta", "#ff00cc"), ("Cyan", "#00c8ff"),
                     ("Amber", "#ffb000"), ("Green", "#00ff40"), ("UV", "#7a00ff"), ("White", "#ffffff")],
        "brief": "A high-energy club night: a warm-up, a build, big drops with strobes, a cool-down.",
    },
    "wedding": {
        "label": "Wedding", "venue": "ballroom",
        "blurb": "Warm uplighting round the room, a soft wash on the top table, movers for the first dance and the party.",
        "lights": [
            ("Uplights", "qlc", "Chauvet/Chauvet-Freedom-Par-Hex-4.qxf", 8, None, ""),
            ("Wash", "qlc", "Martin/Martin-RUSH-Par-2-RGBW-Zoom.qxf", 4, "r3", "spread"),
            ("Movers", "ofl", "chauvet-dj/intimidator-spot-260.json", 2, "r3", "both ends"),
        ],
        "palettes": [("Warm white", "#ffd8a8"), ("Champagne", "#f7e7ce"), ("Blush", "#ffb6c1"), ("Lavender", "#b57edc"),
                     ("Gold", "#ffb000"), ("Party blue", "#0033ff"), ("Party pink", "#ff00cc"), ("White", "#ffffff")],
        "brief": "A wedding: soft warm dinner, a romantic first dance, then a colourful party.",
    },
    "band": {
        "label": "Band / concert", "venue": "concert",
        "blurb": "Washes upstage, beams mid-stage, front light, floor bars and strobes. Song looks and hits.",
        "lights": [
            ("Back wash", "ofl", "robe/robin-ledwash-600.json", 6, "r3", "spread"),
            ("Beams", "qlc", "Clay_Paky/Clay-Paky-Sharpy-Plus.qxf", 4, "r2", "spread"),
            ("Front", "qlc", "Martin/Martin-RUSH-Par-2-RGBW-Zoom.qxf", 6, "r1", "spread"),
            ("Floor bars", "qlc", "Chauvet/Chauvet-COLORband-Q3BT.qxf", 4, None, ""),
            ("Strobes", "qlc", "Chauvet/Chauvet-Shocker-90-IRC-QRG.qxf", 2, "r2", "both ends"),
        ],
        "palettes": [("Red", "#ff0000"), ("Deep blue", "#0033ff"), ("Amber", "#ffb000"), ("Magenta", "#ff00cc"),
                     ("Cyan", "#00c8ff"), ("Green", "#00ff40"), ("Congo", "#7a00ff"), ("White", "#ffffff")],
        "brief": "A rock concert: an intro, verses and big choruses, a ballad, an encore.",
    },
    "theatre": {
        "label": "Theatre", "venue": "theatre",
        "blurb": "Front of house profiles, colour washes over the stage, movers for specials. Scene states as cues.",
        "lights": [
            ("FOH profiles", "ofl", "etc/source-four-led-series-2-lustr.json", 6, "r5", "spread"),
            ("Stage wash", "ofl", "etc/colorsource-par.json", 6, "r2", "spread"),
            ("Back wash", "ofl", "etc/colorsource-par.json", 6, "r4", "spread"),
            ("Specials", "ofl", "robe/robin-ledwash-600.json", 2, "r3", "both ends"),
        ],
        "palettes": [("Warm", "#ffd8a8"), ("Cool", "#cfe3ff"), ("Daylight", "#fff6e8"), ("Sunset", "#ff7a2f"),
                     ("Night", "#1a2a8a"), ("Rose", "#ff6f91"), ("Steel", "#9fb4d9"), ("White", "#ffffff")],
        "brief": "A play: pre-set, a warm day scene, an evening, a night scene, a dramatic moment, curtain call.",
    },
    "corporate": {
        "label": "Corporate event", "venue": "ballroom",
        "blurb": "Clean white light for the stage and speakers, brand-colour uplighting, a lift for awards and the walk-ons.",
        "lights": [
            ("Stage key", "ofl", "etc/source-four-led-series-2-lustr.json", 4, "r3", "spread"),
            ("Stage wash", "ofl", "etc/colorsource-par.json", 4, "r3", "spread"),
            ("Uplights", "qlc", "Chauvet/Chauvet-Freedom-Par-Hex-4.qxf", 8, None, ""),
            ("Movers", "ofl", "chauvet-dj/intimidator-spot-260.json", 2, "r1", ""),
        ],
        "palettes": [("Neutral", "#fff6e8"), ("Brand blue", "#0057b8"), ("Brand teal", "#00a3ad"), ("Purple", "#6a3fa0"),
                     ("Warm", "#ffd8a8"), ("Gold", "#ffb000"), ("Red", "#d0021b"), ("White", "#ffffff")],
        "brief": "A conference with awards: walk-in, speakers, video breaks, award walk-ons, a drinks reception.",
    },
}


def public() -> list[dict]:
    return [{"id": k, "label": t["label"], "blurb": t["blurb"], "venue": t["venue"],
             "lights": sum(x[3] for x in t["lights"])} for k, t in TEMPLATES.items()]


def _install(eng, src: str, key: str) -> int:
    from . import engine as engine_mod
    from . import fixlib, fixtures
    done = fixtures.store_parsed(eng.db_path, fixlib.load(src, key), f"{src}:{key}")
    fixtures.invalidate_cache()
    engine_mod._FIXTURE_CACHE.clear()
    fid = ((done.get("imported") or [{}])[0]).get("fixture_id")
    if not fid:
        raise ValueError(f"{key}: could not be installed")
    return fid


def build(eng, name: str, demo: bool = False) -> dict:
    """The template onto the (empty) desk.  Called from inside the
    show_template action: every step is part of its one undo step."""
    from . import aitools, autoshow
    t = TEMPLATES.get(str(name or "").strip().lower())
    if t is None:
        raise ValueError(f"a template is one of {', '.join(TEMPLATES)}")

    def act(action, **params):
        r = eng.act(action, **params)
        if not r.get("ok"):
            raise ValueError(f"{action}: {r.get('error')}")
        return r
    act("show_new")
    act("venue_template", name=t["venue"])
    notes: list[str] = []
    groups: dict[str, list[int]] = {}
    for gname, src, key, qty, rig, where in t["lights"]:
        try:
            fid = _install(eng, src, key)
        except (ValueError, OSError, KeyError) as exc:
            notes.append(f"{gname}: {exc}")
            continue
        heads = act("add_heads", fixture_id=fid, qty=qty)["heads"]
        if rig:
            r = aitools.place_lights(eng, heads=heads, on=rig, where=where or "spread")
            if not r.get("ok"):
                notes.append(f"{gname}: {r.get('error')}")
        groups.setdefault(gname, []).extend(heads)
    if not groups:
        raise ValueError("no light of this template could be installed: " + "; ".join(notes[:3]))
    for gname, heads in groups.items():
        act("select_heads", heads=heads)
        act("group_create", name=gname)
    # colour palettes on everything that makes colour
    colourful = [h["head_no"] for h in eng.patch if aitools._has(eng, h, "colour")]
    if colourful:
        for pname, hexc in t["palettes"]:
            act("select_heads", heads=colourful)
            act("set_colour", hex=hexc)
            act("record_palette", kind="colour", name=pname)
        act("clear_programmer")
    # position palettes: the movers aimed at each zone of the room
    movers = [h["head_no"] for h in eng.patch if aitools._has(eng, h, "move")]
    if movers:
        from . import venue as venue_mod
        for z in venue_mod.normalise(eng.venue).get("zones") or []:
            act("select_heads", heads=movers)
            if eng.act("aim_at", zone=z["name"]).get("ok"):
                act("record_palette", kind="position", name=z["name"][:24])
        act("clear_programmer")
    act("clear_selection")
    act("quick_defaults", page=1, replace=True)
    # a first set of cues (the offline designer: no AI needed)
    cues = 0
    try:
        d = autoshow.design(eng, t["brief"], offline=True)["design"]
        cues = autoshow.build(eng, d, playback=1).get("built", {}).get("cues", 0)
    except ValueError as exc:
        notes.append(f"cues: {exc}")
    eng.show_file = ""
    if demo:
        # the whole night on the timeline: its cues with their effects, on the tempo
        if not eng.act("timeline_play").get("ok"):
            eng.act("cue_go", playback=1, cue=1)
    return {"template": name, "label": t["label"], "heads": len(eng.patch), "groups": len(groups),
            "palettes": sum(len(v) for v in eng.palettes.values()), "buttons": len(eng.quick), "cues": cues,
            "notes": notes,
            "summary": f"{t['label']}: {len(eng.patch)} lights, {len(groups)} groups, "
                       f"{sum(len(v) for v in eng.palettes.values())} palettes, {len(eng.quick)} buttons, {cues} cues"}
