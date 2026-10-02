# Handoff: where the work stands (2026-10-02)

Read this first in a new Claude Code session on this repo.  It carries the
context of the long session that built the full debugging tools.

## How the operator works

- Speak plainly: they're a lighting operator, not a developer.  Say what a
  change means on the desk and on the real lights.
- Merge flow, after each finished batch of work:
  1. Push to the session branch.
  2. Open a PR to `main`.
  3. Merge it (merge commit).
  4. Reset the branch to `origin/main`.
- `.env` holds the AI key: never commit it, never print it.  `data/` is
  gitignored (fixtures DB, shows, memory).  Back it up before browser
  tests that touch a running server; restore after.
- Desktop only (no phone / tablet layouts).
- Use the project's skills (copied into `.claude/skills/`, so no plugin
  download is needed - see `.claude/skills/_licenses/README.md`):
  - **ui-ux-pro-max** and **design-for-ai** for anything on screen;
  - **agent-skills** for debugging, tests, review and git;
  - **ponytail** for review, audit and debt.
  Before each part of the work, open the matching SKILL.md and follow it.

## The debugging tools (all in `tools/`)

| Tool | What it does | Time |
|---|---|---|
| `selftest.py` | 2,921 engine / server / web checks (CI) | ~40 s |
| `gigcheck.py` | A whole gig, then save → reload → compare (CI) | <1 s |
| `libsweep.py` | All 2,424 library lights × every control, DMX vs 3D | ~20 s |
| `uicheck.mjs` | Every screen and dialog in a browser at 1280 / 1440 / 1920; flags page errors, spills, tiny or off-screen controls, empty panels; screenshots | ~20 min at one width with `--widths 1440` |
| `rigcheck.py`, `rigcheck_3d.mjs` | Every light type placed every way; beam lands where aimed | |

`uicheck.mjs` starts its own server on a scratch database, so it never
touches `data/`.  Run it with an output folder, e.g.
`node tools/uicheck.mjs /tmp/uic --widths 1440`.

## Done in this session (all merged unless noted)

- **Hold buttons ("Make a button…").**  A button now keeps everything the
  lights show: brightness, colour or a colour effect (rainbow), gobo and
  beam, movement or roam, and what a cue gives them.  It holds the lights
  until turned off, and the tile says what it keeps.
- **FX tab.**  Speed and size are set with tap buttons
  (Slow / Medium / Fast, − / +, ½× / 2×, a typed number) and change live
  (`fx_tweak`).
- **Buttons screen.**  The dock is taller and can be resized by dragging.
- **Library sweep fixes:**
  - colour effects work on CMY and white-only lights;
  - brightness effects work on shutter-only lights;
  - LED lights are no longer mistaken for effect machines;
  - fog and haze outputs are found;
  - Locate on CMY lights comes out white.
- **Show files keep their tempo.**  Pushed to the session branch, not
  merged yet (it goes with this handoff).

## Left to do

1. **Fix the screen-check findings at 1440**, then run all three widths.
   Real problems first:
   - **Top bar overflows.**  Help (?), Settings and Design run off the right
     edge and the page scrolls sideways (7 screens).  The bar needs to
     shrink or wrap: hide labels into icons, or move items into a menu.
   - **Too small to click:**
     - the Help and Settings icons;
     - the × on chips;
     - the link buttons "Teach the gobo…", "Move tab" and
       "Delete from library";
     - the timeline length;
     - checkboxes (13 px).
   - **Text spills** on the laser mode chips (two lines in a 22 px chip)
     and on the "GDTF Share" tab.
   - **Script fixes:**
     - the Venues menu and Arrange "Done" steps time out (over 20 s);
     - the off-screen chips under the command bar and the profile
       editor are behind a modal (left of x=0).

   Check both of the last two before "fixing" anything.
2. **Odd inputs** (not built yet):
   - empty rig;
   - 500+ lights;
   - unusual modes;
   - a corrupt show file;
   - dropped connection;
   - two browsers at once;
   - Undo after everything.
3. **Real-light checklist** for the operator: about 15 minutes with their
   Art-Net node.  Check each light, colours, Locate, movement speed,
   lasers and fog, and blackout.  They report back with notes or
   screenshots.
4. **Backlog** (`docs/BACKLOG.md`), add as A8:
   - **Combo lights with a laser (33):** the laser module has no "on"
     channel the console finds.  This is a safety question, so don't
     guess an output; it stays ARM-gated.
   - **Vari-Lite "Blue / Amber / Magenta Mixer"** subtractive channels
     (2 lights).
   - **A CMY-only light with no dimmer and no shutter** shows dark in 3D on
     Full (1 light).
   - **Shutter flicker / random strobe** never change on 3 lights.
