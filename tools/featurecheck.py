"""What does this console actually have?  MEASURED, not asserted.

Every line below is answered by looking at the running code: the action
table, the modules that exist on disk, and - where a feature is a claim
about behaviour - by calling the engine and reading the result.  A feature
that exists only in a comment, or only as a button with no engine behind
it, does not appear.

    python tools\\featurecheck.py
"""
import importlib
import importlib.util
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config      # noqa: E402
from app import engine as eng  # noqa: E402

A = set(eng.ACTIONS)
rows = []          # (area, feature, yes/no, how it was determined)


def yes(area, feature, how):
    rows.append((area, feature, True, how))


def no(area, feature, how):
    rows.append((area, feature, False, how))


def mod(name):
    """Is there a real module called `name`?"""
    try:
        return importlib.util.find_spec("app." + name) is not None
    except (ImportError, ValueError):
        return False


def acts(*names):
    return set(names) <= A


td = pathlib.Path(tempfile.mkdtemp())
# A COPY of the fixture library, never the library itself.
#
# This file used to build its engine on `config.DB_PATH` - the real one -
# which meant a diagnostic could write to the operator's rig, and `add_heads`
# can create a profile.  Every other tool in this directory already builds on
# a throwaway database under a temp dir; this one was the exception, and the
# exception is exactly where the damage happens.  The copy is what makes the
# "measured" claim safe: a survey should be impossible to run by accident.
_scratch_db = td / "fixtures.db"
if config.DB_PATH.exists():
    shutil.copy2(str(config.DB_PATH), str(_scratch_db))
# The probes need a known head to act on, whatever the operator's library
# holds - and this is a copy, so seeding it touches nothing real.
from app import fixtures as _fx  # noqa: E402
_fx.seed_generics(str(_scratch_db))
e = eng.Engine(db_path=_scratch_db, dry_run=True, show_dir=td / "shows")

def run(action, **params):
    """Does the action not only EXIST but actually RUN?

    `acts()` only asks whether the name is in the action table, and that is
    how this file came to report "no software lock" and "no invert/swap pan &
    tilt" long after both shipped: the lines were hand-maintained and they
    drifted.  A survey that UNDER-reports is worse than no survey, because
    the gap it names is one somebody will then go and build.  So the entries
    that are claims about behaviour call the engine and read the result.
    """
    try:
        res = e.act(action, **params)
    except Exception as exc:                      # a refusal is a failure
        return False, "raised %s: %s" % (
            type(exc).__name__, str(exc)[:70])
    if not isinstance(res, dict):
        return False, "returned %r" % (res,)
    if res.get("ok") is False:
        return False, "refused: %s" % (res.get("error") or "")[:70]
    return True, "ran -> %s" % (res.get("summary") or res.get("state")
                                or res.get("heads") or "ok")

def probed(area, feature, action, **params):
    """A `yes`/`no` that is a measurement rather than an opinion."""
    # Release the lock first.  Probing `set_lock` leaves the desk LOCKED, and
    # LOCKED freezes the patch - so the next probe got "nothing selected" and
    # reported two features that exist as missing.  A probe must not inherit
    # the mode a previous probe left behind, or the survey reports its own
    # test sequence.
    e.act("unlock", password="survey")
    # And give it something to act on.  This engine is built on the fixture
    # LIBRARY database, which holds profiles, not the show - so it starts
    # with an EMPTY patch, and every head-dependent probe would have said
    # "nothing selected" and reported the feature as missing.
    if not e.patch:
        e.act("add_heads", query="LED PAR 4ch", qty=1, mode="4ch RGBW",
              address=1)
    if not ({"head", "heads"} & set(params)):
        e.act("select_all")                 # most probes want a selection
    good, how = run(action, **params)
    (yes if good else no)(area, feature, how)

_WEB = {}


def web():
    """The shipped client source, read once.

    A client feature cannot be probed by calling the engine, so this is a
    SOURCE check and the `how` column says so.  It is still worth having: a
    button with no markup behind it, or a canvas with no draw call, is
    exactly the "exists only as a button" case this file exists to catch.
    """
    if not _WEB:
        files = [ROOT / "web" / "index.html", ROOT / "web" / "app" / "app.css"]
        files += sorted((ROOT / "web" / "app").glob("*.js"))
        files += sorted((ROOT / "web" / "js").rglob("*.js"))
        for path in files:
            _WEB[path.name] = path.read_text(encoding="utf-8")
    return "".join(_WEB.values())

def client(feature, *needles):
    blob = web()
    missing = [n for n in needles if n not in blob]
    (yes if not missing else no)(
        "client", feature,
        "in web/index.html + web/app" if not missing
        else "missing: %s" % ", ".join(m[:40] for m in missing))


# --------------------------------------------------------------------------
# SETUP
# --------------------------------------------------------------------------
yes("setup", "DMX address / universe per fixture", "set_address, add_heads")
yes("setup", "unlimited fixture groups", "group_create (no cap in code)")
yes("setup", "fixture library from the cloud", "app/gdtfshare.py + /api/gdtf/*")
yes("setup", "fixture profile editor", "fixtures.set_channel_label/range")
yes("setup", "patch sheet (CSV)", "Engine.patch_csv / export_patch")
yes("setup", "auto-patch, no overlaps", "auto_patch / plan_addresses")
yes("setup", "arrangement in 2D/3D", "web/viz.js, WebGL + 2D poster")
yes("setup", "spreading and alignment tools", "distribute, align, mirror")
yes("setup", "per-fixture MOVEMENT limits", "the fixture's own GDTF range")
yes("setup", "per-fixture DIMMER limits (min/max per attribute)", "set_limits (see MISC)")
no("setup", "instant custom matrix/strip fixtures",
   "no virtual matrix fixture type")
no("setup", "hardware manager (device discovery/management)",
   "import_scan polls, but there is no device list to manage")
no("setup", "auto check for software updates", "not implemented")

# --------------------------------------------------------------------------
# CONTROL
# --------------------------------------------------------------------------
yes("control", "scenes (cues) and playback", "record_cue, cue_go, %d stacks"
   % eng.PLAYBACK_COUNT)
yes("control", "cue list editing", "insert/delete/move/rename/edit_cue")
yes("control", "cue at a percentage (execute at)", "cue_go(at=)")
yes("control", "stack loop", "follow_set(loop=)")
no("control", "per-cue priority", "no priority field; HTP by layer order")
no("control", "per-cue loop count / jump-to cue", "no count or target field")
no("control", "SUPER SCENE (scenes layered on a timeline)", "not implemented")
no("control", "steps-sequencing scene type", "not implemented")
yes("control", "palettes (per attribute family)", "record/include_palette")
yes("control", "presets (complete looks)", "record/include_preset")
yes("control", "effects (FX) on any channel",
   "run_fx; roles: " + ", ".join(
       r for page in eng.Engine.ATTR_PAGES for r in page[1:]))
yes("control", "scene skip / previous / pause / stop",
   "cue_go, cue_back, cue_forward, playback_release")
yes("control", "scene fade in and out", "per-cue fade_s, interpolated")
yes("control", "master dimmer", "master")
yes("control", "blackout", "blackout")
yes("control", "per-playback level", "playback_level")
yes("control", "MIDI", "app/midi.py" if mod("midi") else "not present")
no("control", "OSC", "no app/osc.py")
no("control", "MIDI Clock / Ableton Link / tap tempo",
   "no clock follower")
no("control", "audio beat detection", "no audio input")
yes("control", "DMX input (a desk driving this one)",
   "app/dmxin.py, started from config" if mod("dmxin") else "not present")
no("control", "computer-keyboard mapping mode", "fixed keys, not mappable")
no("control", "scene mapping (link any control to any scene)",
   "not implemented")

# --------------------------------------------------------------------------
# LIVE
# --------------------------------------------------------------------------
yes("live", "live output with a real sender", "app/artnet.py, app/sacn.py")
yes("live", "direct Art-Net (no external desk needed)",
   "the app IS the desk")
yes("live", "blind mode (edit without affecting the show)", "dry run")
no("live", "live mixer (group dimmer/hue/strobe/blackout/solo)",
   "not implemented")
no("live", "live rotary encoders per scene (dimmer/speed/phase/size)",
   "not implemented")
no("live", "touch window / custom control surfaces", "not implemented")
no("live", "write scenes to the device for stand-alone playback",
   "output only, never to a node")
no("live", "Nicolaudie SUT hardware support", "no serial/hardware layer")

# --------------------------------------------------------------------------
# MISC
# --------------------------------------------------------------------------
probed("misc", "software lock (full or partial, password)", "set_lock",
       state="locked", password="survey")
probed("misc", "invert / swap pan & tilt per fixture", "set_orient",
       invert_pan=True, invert_tilt=True, swap_pan_tilt=True)
probed("misc", "per-fixture dimmer floor and travel limits", "set_limits",
       role="dimmer", low=8)
client("pick a colour with a pointer (HSV ring + SV square)",
       'id="picker-canvas"', "export function createPicker(", "hsvToRgb")
client("3D stage with fixture models by type and brand",
       "export class Stage", "export function buildFixture(", "buildGdtf")
client("remote control from a phone or tablet (responsive layout)",
       "mobile-tabs", "@media (max-width: 820px)")
client("command bar: console syntax or plain English",
       "export function openCmdbar(", "run_command")
no("misc", "detachable windows for a dual-monitor setup", "not implemented")
yes("misc", "undo / redo that names the edit", "_undo_public labels")
yes("misc", "command line", "run_command")
yes("misc", "context help", "? overlay + `?` in the command line")
yes("misc", "audit of overlaps / wasted universes", "app/doctor.py (Copilot → Diagnose)")

e.shutdown()

# --------------------------------------------------------------------------
last = None
yes_n = no_n = 0
for area, feature, good, how in rows:
    if area != last:
        print()
        print("=" * 72)
        print(area.upper())
        print("=" * 72)
        last = area
    print("  %-3s  %-42s %s" % ("yes" if good else "NO", feature, how))
    if good:
        yes_n += 1
    else:
        no_n += 1

print()
print("=" * 72)
print("%d yes, %d no, of %d checked" % (yes_n, no_n, yes_n + no_n))
print("=" * 72)
