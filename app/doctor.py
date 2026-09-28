"""The rig doctor: the checks a lighting tech runs before doors.

Each finding says what is wrong, why it matters on the night, and - where
the desk can fix it safely - offers the engine action that does.  Pure
reads of the engine state; nothing here changes the show.

Levels: `error` (it will not work), `warn` (it will surprise you), `info`
(worth knowing), `ok` (checked and fine).
"""
from __future__ import annotations

from . import config, fixture_kind


def _finding(level: str, title: str, detail: str = "", fix: dict | None = None) -> dict:
    out = {"level": level, "title": title, "detail": detail}
    if fix:
        out["fix"] = fix
    return out


def examine(eng) -> dict:
    """Findings for the current rig, show and output, worst first."""
    with eng.lock:
        patch = [dict(h) for h in eng.patch]
        playbacks = eng.playbacks
        stale = eng._stale_heads(eng.patch, eng.playbacks)
        dry_run, live = eng.dry_run, eng.live
        cues = sum(len(pb.get("stack") or []) for pb in playbacks)
        groups = len(eng.groups)
        venue = dict(eng.venue or {})
    out: list[dict] = []

    if not patch:
        out.append(_finding("error", "Nothing is patched",
                            "Add fixtures from the library first - the desk has nothing to drive."))
        return _wrap(out)

    # -- addressing -------------------------------------------------------
    used = sorted({h["universe"] for h in patch})
    gaps = [u for u in range(1, (used[-1] if used else 0) + 1) if u not in used]
    if gaps:
        out.append(_finding(
            "warn", f"Universe{'s' if len(gaps) > 1 else ''} {', '.join(map(str, gaps[:6]))} unused",
            "The patch skips universes, so a node has to be configured for numbers nothing uses. "
            "Auto-addressing packs the rig from 1.001 with no gaps.",
            {"label": "Pack the addresses", "action": "auto_patch", "params": {}}))
    footprint = sum(int(h.get("channels") or 0) for h in patch)
    capacity = len(used) * 512
    if capacity and footprint / capacity < 0.4 and len(used) > 1:
        out.append(_finding(
            "info", f"{len(used)} universes carry {footprint} channels",
            f"That is {footprint * 100 // capacity}% full; the rig would fit in "
            f"{-(-footprint // 512)} universe(s).",
            {"label": "Pack the addresses", "action": "auto_patch", "params": {}}))

    # -- profiles ---------------------------------------------------------
    guessed = [h["head_no"] for h in patch if h.get("unverified")]
    if guessed:
        out.append(_finding(
            "warn", f"{len(guessed)} fixture(s) use a guessed channel layout",
            "They were patched from a generic profile. If the real fixture's channels are in a "
            "different order, it will do the wrong thing. Heads: "
            + ", ".join(f"#{n}" for n in guessed[:12]) + ". Download the real profile from the GDTF Share."))
    raw = [h["head_no"] for h in patch if "raw" in (h.get("map") or [])]
    if raw:
        out.append(_finding(
            "info", f"{len(raw)} fixture(s) have channels with no control",
            "Some channels have a label the desk does not recognise, so nothing drives them. "
            "Rename them in the fixture profile editor. Heads: " + ", ".join(f"#{n}" for n in raw[:12])))

    # -- the show ---------------------------------------------------------
    if stale:
        out.append(_finding(
            "error", f"Cues point at {len(stale)} fixture(s) that are not patched",
            "Those parts of the cues do nothing. Re-patch the heads or re-record the cues. "
            "Missing: " + ", ".join(f"#{n}" for n in stale[:12])))
    if not cues:
        out.append(_finding("info", "No cues recorded yet",
                            "Set a look, then Record cue - or let the copilot design a show from a brief."))
    if not groups and len(patch) >= 6:
        out.append(_finding("info", "No groups",
                            "Groups make big rigs fast to program: select a row of lights and press G."))

    # -- the room ---------------------------------------------------------
    spots: dict[tuple, list[int]] = {}
    for h in patch:
        key = (round(float(h.get("x", 0)), 1), round(float(h.get("y", 0)), 1),
               round(float(h.get("z", 0)), 1))
        spots.setdefault(key, []).append(h["head_no"])
    piled = [heads for heads in spots.values() if len(heads) > 1]
    if piled:
        out.append(_finding(
            "warn", f"{sum(len(p) for p in piled)} fixtures share a position",
            "They are drawn on top of each other on the stage, so the 3D view cannot show what "
            "each is doing. Drag them apart, or use Tools → Spread. "
            + "; ".join(" ".join(f"#{n}" for n in p[:6]) for p in piled[:4])))
    width = float(venue.get("width_m") or 0)
    if width:
        outside = [h["head_no"] for h in patch if abs(float(h.get("x", 0))) > width / 2 + 2]
        if outside:
            out.append(_finding("warn", f"{len(outside)} fixture(s) hang outside the stage",
                                "They are more than 2 m beyond the stage width you set. "
                                + ", ".join(f"#{n}" for n in outside[:12])))

    # -- output -----------------------------------------------------------
    host = config.DMX_HOST
    if dry_run:
        out.append(_finding("info", "Blind mode: nothing reaches the rig",
                            "Frames are built and counted, but nothing is sent. Use Go live when "
                            "the node is connected."))
    elif not live:
        out.append(_finding("warn", "Output is stopped",
                            "The desk is allowed to send, but the output is not running. "
                            "Press Go live."))
    if host == "255.255.255.255":
        out.append(_finding("warn", "Output is a global broadcast",
                            "255.255.255.255 is often dropped by switches and Wi-Fi. Leave DMX_HOST "
                            "empty to send to your lighting subnet, or set a node's IP."))
    elif host in ("127.0.0.1", config.LOCAL_IP) and config.DMX_TRANSPORT == "artnet":
        out.append(_finding("error", "DMX is being sent to this computer",
                            f"DMX_HOST is {host}, which is this machine - no node will receive it. "
                            "Leave DMX_HOST empty, or set the node's IP."))
    types = {fixture_kind.describe(h)["type"] for h in patch}
    if types <= {"atmos"}:
        out.append(_finding("warn", "Only haze machines are patched", ""))

    if not any(f["level"] in ("error", "warn") for f in out):
        out.insert(0, _finding("ok", "No problems found",
                               f"{len(patch)} fixtures on {len(used)} universe(s), {cues} cue(s)."))
    return _wrap(out)


def _wrap(findings: list[dict]) -> dict:
    order = {"ok": -1, "error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["level"], 9))
    errors = sum(1 for f in findings if f["level"] == "error")
    warns = sum(1 for f in findings if f["level"] == "warn")
    summary = ("Ready." if not errors and not warns
               else f"{errors} problem(s), {warns} warning(s).")
    return {"findings": findings, "summary": summary,
            "errors": errors, "warnings": warns}
