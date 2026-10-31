# Handoff: where the work stands (2026-10-21)

Read this first in a new Claude Code session on this repo.  The plan is
`docs/BACKLOG.md`, section **A15** (B1 to B7).  B1 is merged (PR #108);
the operator's last word was **"do all"**: carry on with B2 to B6, one PR
each, merging each one when it is green.  B7 waits for the operator.

## How the operator works

- They are a lighting operator (clubs, DJ gigs), not a developer.  Write in
  plain English: say what a change means on the desk and on the real lights.
- Say which skills you used, if any.
- **Merge flow** after each finished batch:
  1. Push to the session branch (here `claude/trusting-pascal-20perv`; use
     the branch your session is given).  Never force-push.
  2. Open a PR to `main`.
  3. When CI is green, merge with a merge commit (`expectedHeadSha` is the
     full 40-character SHA).
  4. Fast-forward the branch to `origin/main` and push.
- No model names in commits, PRs or code.
- **Rules:**
  - `.env` holds the AI key: never commit it, never print it.
  - `data/` is gitignored.
  - Tests and tools run on scratch data (`CONSOLE_DATA_DIR`), never on
    `data/`.
- Desktop only, no phone or tablet layouts.  The screens say "the desk",
  never the app's name (that is `APP_NAME` in `.env`).
- The operator's hardware: Chauvet Intimidator Wave 360 IRC (33 ch),
  Intimidator Spot 260 (14 ch, OFL profile), Funfetti Shot, Windows 11, the
  desktop (Electron) app.

## Running the checks

```
export CONSOLE_DATA_DIR=$(mktemp -d) CONSOLE_AUTOSAVE=false PYTHONIOENCODING=cp1252
python3 tools/selftest.py             # ~3,860 checks, ~1 min
ruff check --select F app tools
python3 tools/oddcheck.py             # ~16,000 checks, under 1 min
node tools/uicheck.mjs screencheck --widths 1440   # ~10 min; nightly runs 1280/1440/1920
for f in web/app/*.js; do node --check $f; done
```

CI runs selftest (Ubuntu and Windows, Python 3.11 and 3.13), lint,
screen-check, library-rules and frame-timing.  See `docs/DEVELOPING.md`
for the other tools (`gigcheck`, `libsweep`, `vischeck`, `brands.py`...).

## Things that bite

- **A new engine action `_a_<name>`** must be listed in:
  - `engine_base.ACTIONS`;
  - `UNDO_EXCLUDED`, if it is played rather than edited;
  - an AI tier in `aitools` (`MORE` / `CONFIRM` / `NEVER`);
  - `console_ai.DENY_ACTIONS`, for safety;
  - the `engine_rig` lock lists, if it is setup.

  It also needs a way in on screen, or `test_look_and_reach` fails (the
  `elsewhere` set in `tools/selftests/part09.py`).
- **New selftests** go in `tools/selftests/part09.py`.  Register each in
  **both** lists in `tools/selftest.py` (the import list and the named
  list).
- **Loading a library light in a test:**
  `fixtures.store_parsed(db, fixlib.load(src, key), f"{src}:{key}")`, then
  `eng.Engine(db_path=db, dry_run=True, show_dir=...)` and
  `e.act("add_heads", query=..., mode=...)`.  A DMX frame is
  `e.build_frames()[universe]`; the 3D feed is `e._looks()`.
- **Line endings:** the repo stores LF; `.gitattributes` checks .py, .js
  and .bat out as CRLF.  If an exact-text edit fails, strip `\r` first.
- **Processes:** never `pkill` / `pgrep -f` a pattern that is in your own
  command line; it kills your shell (exit 144).
- **Browser checks:** use one port per run, and never run two browser
  checks at once.
- **Network:** chauvetdj.com, manualsnet and GitHub issue attachments
  (the operator's bug-report zips) are blocked from the cloud container.
  Ask the operator for screenshots or text instead.
- **Windows clock:** it ticks every 16 ms, so anything timed by
  `time.monotonic()` can see "no time passed".

## Done in B1 (PR #108, merged)

- **#107 confetti:** Shot, Hold to fire and On / off buttons; the
  programmer has the same plus STOP and Make a button (`web/app/fxpanel.js`
  `fireExtras`, owners `prog-*`).  The FX page makes a hold button and an
  on / off button.
- **#106 Wave 360 extra colours:** not confirmed.  The DMX matches the QLC
  33ch profile; the suspects are the light's menu being in 17ch (red would
  land on its Color Wheel macro channel) or a wrong profile.  When a colour
  is wrong, *Test this light* now lights each colour channel on its own
  (`colourChannels` in `web/app/lightdialogs.js`).
  **Waiting on the operator's result from the real light.**
- **#105 Spot 260:**
  - a *Desk strobe* row in the programmer flashes the level from the desk
    (`desk_strobe` action, cleared by Clear, `snap.desk_strobe`);
  - a Closed shutter now shows dark in 3D (`_gate_closed`, engine_looks).
  - "3D strobes by default" was **not reproduced**; ask for steps or a
    screenshot.
- **Per-head colour:** tapping a head picks it alone (Ctrl / Shift adds),
  with Odd / Even chips and a colour dot per head (`renderProgCells`).
  The engine was already right; the screen's taps were adding heads.
- **Truss turn:** `venue_update` returns `turned: {head: old yaw}`; the
  screen asks "Did the real truss turn too?", and *No* calls `keep_facing`
  (`askTurned` in `web/app/venuepanel.js`).
- The issues are still open on GitHub (the PR says "Refs").  Close them
  once the operator confirms on the real lights.

## Next: B2 to B6 (details in `docs/BACKLOG.md` A15)

- **B2 Groups:**
  - add and remove lights in an existing group, and rename it; today only
    `group_create` / `group_delete` exist (`app/engine_select.py`);
  - stop the auto show making `"Auto · "` copy groups
    (`app/autoshow.py` `compile_calls`, `AUTO_PREFIX`).  Use the
    operator's groups, or select the lights directly, and offer to tidy
    the copies already made.  The timeline tracks also carry the prefix;
    keep tracks recognisable, but don't create groups.
- **B3 Buttons and pages, MagicQ style:**
  - editing a button opens it in the programmer, with *Update button*
    (`quick_from_programmer` exists, no way back);
  - named pages; label-only, go-to-page and folder buttons; sizes 1 to 4
    wide and tall; colour, icon, drag to arrange;
  - make colour-only buttons obvious ("Colour · 4 lights");
  - prebuilt programs adjustable (odd / even, 2 of 4 off, order, pan and
    tilt offsets).
  - Code: `app/engine_quick.py`, `web/app/quickbuttons.js`.
  - Also: picking a colour with lights at 0% leaves them dark
    (`set_colour` doesn't touch the dimmer); bring them up or say so.
- **B4 Movement:**
  - moves in real degrees (e.g. pan −100° to +40°), loop / bounce / once,
    speed, no 90 / 180 / 270 stops, "full" working;
  - a phase spread for a smooth wave across heads;
  - per-head moves on multi-head lights;
  - the preview shows the degrees;
  - simpler Wave 360 controls.
- **B5 Visualiser:**
  - knows the front, the centre and the DJ booth; dragging, the camera
    and the Move pad work from the booth's point of view;
  - the Move plan view updates when objects move, and can rotate;
  - fix map / 3D tracking.
- **B6 Layouts:**
  - a layout with no visualiser at all;
  - full-screen buttons fully customisable;
  - 720p fixes (relative dock height, group faders fold, fixture list at
    least 5 rows, panel minimum sizes, Live first, fewer built-in layouts,
    a Theatre cue list);
  - audit fixes: Record cue gives a reason, Ready-check fixes as buttons,
    a Mobile DJ template, tours per mode.
- **B7 AI bugs:** the operator will list them later.  Don't start until
  they do.

## Map of the code

- **Engine:** `app/engine*.py`, a stdlib Python engine split by area:
  - `engine_quick` (buttons), `engine_fxlayer` (special effects, light
    test, strobe), `engine_looks` (the 3D feed), `engine_rig` (venue,
    locks), `engine_select` (groups), `engine_program`, `engine_desk`;
  - `app/merge.py` is the HTP / LTP merge and the overrides;
  - fixture files come from `app/fixlib` (OFL, QLC and haze libraries).
- **Server:** `app/main.py`.
- **Screen:** `web/app/*.js` (programmer, fxpanel, quickbuttons,
  venuepanel, lightdialogs...), the 3D in `web/js/stage/`, styles in
  `web/app/app.css`.
- **AI:** `app/console_ai.py`, `app/aitools.py`, `app/assistant.py`,
  `app/autoshow.py` (shows per song); rekordbox import in
  `app/rekordbox.py`.
- **Docs:** `docs/ARCHITECTURE.md`, `docs/DEVELOPING.md`, `docs/GUIDE.md`
  (the user guide), `docs/REAL_LIGHT_CHECKLIST.md`.
