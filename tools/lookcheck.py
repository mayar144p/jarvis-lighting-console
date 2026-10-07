"""Does the 3D show every range of every channel?  For each model of the
test rig (tools/bigrig.mjs), every channel is stepped through each range
its fixture file lists, and the light feed is checked against the file's
words:

    MISSED  the words say something the 3D can show (a prism going in,
            a gobo shaking, a split colour, a pulse) and the feed doesn't
            say it - a bug
    not drawn  words the 3D has nothing for yet (animation wheels,
            macros, a second gobo wheel's pictures) - listed, not a fail

    python tools/lookcheck.py            the test rig
    python tools/lookcheck.py sharpy     only models whose name matches

Runs on its own scratch data; the network only to fetch library files
not already cached.  Exit 1 when anything is MISSED.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# words with nothing to show: a closed, open or idle range
QUIET = re.compile(r"^\s*(open|closed?|off|on|light on|nothing|no function|none|empty( position)?|idle|free|stop( rotation)?|"
                   r"reserved|not used|unused|default|light off|blackout|shutter (open|closed)|"
                   r"(gobo|colou?r) \d+|.*\bopen\b.*)\s*$", re.I)
EXPECT = [
    ("prism", re.compile(r"\d+\s*-?\s*facet|prism (in|into|on)\b|linear prism", re.I), lambda lk, r: lk.get("prism")),
    ("shake", re.compile(r"shak|wobbl|vibrat", re.I), lambda lk, r: lk.get("gshake")),
    ("turning", re.compile(r"rotat|spin|index|scroll|rainbow|continuous|flow", re.I),
     lambda lk, r: lk.get("prot") or lk.get("grot") or lk.get("gscroll") or lk.get("cscroll") or r.get("spin")),
    ("split", re.compile(r"\S\s*[+/&]\s*\S", re.I), lambda lk, r: lk.get("split")),
    ("strobe kind", re.compile(r"puls|random|ramp|slow on|slow off|even on|gradual|fade on", re.I),
     lambda lk, r: lk.get("smode")),
    ("strobe", re.compile(r"strob|stobe", re.I), lambda lk, r: r.get("hz") or lk.get("shz")),
]
# words for something more than a plain colour or gobo slot
SPECIAL = re.compile(r"snap|sound|auto|program|random|macro|effect|fade|flow|vibra|shak|strob|puls|ramp|"
                     r"rotat|scroll|index|spin|music|chase|stobe", re.I)
# channels whose words are about something else (a speed, a mode, a macro)
SKIP_ROLES = re.compile(r"^(pan|tilt|speed|macro|dimmer|red|green|blue|white|amber|uv|lime|cyan|magenta|yellow|"
                        r"zoom|focus|frost|iris|cto|.*_fine)$")


def rig() -> list[tuple[str, str, str]]:
    text = (ROOT / "tools" / "bigrig.mjs").read_text(encoding="utf-8")
    return re.findall(r'\["(\w+)", "([^"]+)", "([^"]+)", \d+\]', text)


def main(argv: list[str]) -> int:
    from app import beamlook
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # a file's "°" or "‘" on a Windows console
    from app import engine as eng
    from app import fixlib, fixtures
    only = " ".join(argv).lower()
    missed_total, drawn, quiet_n, gaps, plain = 0, 0, 0, 0, 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            for src, key, name in rig():
                if only and only not in name.lower():
                    continue
                try:
                    done = fixtures.store_parsed(db, fixlib.load(src, key), f"{src}:{key}")
                except (OSError, ValueError) as exc:
                    print(f"{name}: can't load ({exc})")
                    continue
                fid = (done.get("imported") or [{}])[0].get("fixture_id")
                heads = e.act("add_heads", fixture_id=fid, qty=1).get("heads") or []
                if not heads:
                    print(f"{name}: can't patch")
                    continue
                h = e._head(heads[0])
                n = h["head_no"]
                ranges = e.head_ranges(h)
                e.act("select_heads", heads=[n])
                e.act("set_intensity", level=100)
                lines = []
                if e._head_class(h) != "light":
                    print(f"{name}: an effects machine (the effects layer draws it)")
                    e.act("patch_clear")
                    continue
                shutter = e._shutter_role(h)
                other = []
                for role, rng in ranges.items():
                    if role not in h["map"] or SKIP_ROLES.match(role):
                        continue
                    label = rng.get("name") or role
                    ours = role in ("prism", "gobo", "gobo_rot", "wheel", shutter) or (
                        role.startswith("aux") and any(rx.search(str(label)) for rx in beamlook._AUX_NAME.values()))
                    caps = [c for c in rng.get("caps") or [] if str(c[2] or "").strip()]
                    if not ours or len(caps) < 2:
                        if len(caps) >= 2:
                            other.append(label)
                        continue
                    for lo, hi, words in caps:
                        words = str(words)
                        if QUIET.match(words):
                            quiet_n += 1
                            continue
                        e._set_programmer(n, role, (int(lo) + int(hi)) // 2)
                        row = next(r for r in e._looks() if r["n"] == n)
                        lk = row.get("look") or {}
                        want = [(what, has) for what, rx, has in EXPECT if rx.search(words)
                                and not (what == "split" and not beamlook._split(words))
                                and not (what == "turning" and re.search(r"\bstop", words, re.I))
                                and not (what.startswith("strobe") and role != shutter)]
                        if not want and role != shutter and not SPECIAL.search(words):
                            plain += 1               # a colour or a gobo slot: drawn as such
                            continue
                        if not want:
                            gaps += 1
                            lines.append(f"    not drawn  {label}: {words}")
                            continue
                        for what, has in want:
                            if has(lk, row):
                                drawn += 1
                            else:
                                missed_total += 1
                                lines.append(f"    MISSED     {label} [{role}] {lo}-{hi} '{words}': no {what} in the feed {lk}")
                    e._set_programmer(n, role, 0)
                if other:
                    lines.append(f"    other channels (programs, modes, speeds; not drawn): {', '.join(other)}")
                print(f"{name} ({h.get('mode')})")
                print("\n".join(lines) if lines else "    every range shown")
                e.act("patch_clear")
        finally:
            e.shutdown()
    print(f"\n{drawn} ranges shown, {plain} plain colour / gobo slots, {missed_total} MISSED, "
          f"{gaps} not drawn yet, {quiet_n} with nothing to show")
    return 1 if missed_total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
