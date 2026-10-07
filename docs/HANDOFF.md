# Handoff: where the work stands (2026-10-07, third session)

Read this first in a new Claude Code session on this repo.

## How the operator works

- Speak plainly: they're a lighting operator, not a developer.  Say what a
  change means on the desk and on the real lights.
- Merge flow, after each finished batch of work:
  1. Push to the session branch.
  2. Open a PR to `main`.
  3. Merge it (merge commit) once CI is green.
  4. Reset the branch to `origin/main`.
- `.env` holds the AI key: never commit it, never print it.  `data/` is
  gitignored.  Tests and tools run on scratch data (`CONSOLE_DATA_DIR`),
  never on `data/`.
- Desktop only (no phone / tablet layouts).
- The name is subject to change: it is `APP_NAME` in `.env` (title and top
  bar).  The screens' own sentences say "the desk", never the name.
- Use the project's skills (in `.claude/skills/`) and say which one you use
  for each part:
  - **ui-ux-pro-max** and **design-for-ai** (usability, clarify, prototype)
    for anything on screen;
  - **agent-skills** for debugging, tests, review and git;
  - **ponytail** for the simplest fix, review, audit and debt;
  - **fixture-debug** when the operator names a brand or a product to
    debug ("debug Chauvet", "the Antari Z-1000").
- Line endings: the repo checks files out CRLF.  `git stash` rewrites the
  working files CRLF, which breaks exact-text edit scripts; strip `\r`
  first (`sed -i 's/\r$//' file`).

## The checks (all in `tools/`, see the README's Tests section)

| Check | What it proves |
|---|---|
| `selftest.py` | 2,918 engine / server / web checks (CI, Linux + Windows) |
| `gigcheck.py` | a whole gig, saved, reloaded, compared (CI) |
| `oddcheck.py` | empty rig, 12,900 nonsense values, 520 lights, 23 corrupt shows / autosaves, restart, Undo all the way (CI) |
| `libsweep.py [--all-modes]` | every library light / mode (8,093): Full, Blackout, colour, gobo, move, Locate, effects, SFX - DMX vs 3D |
| `uicheck.mjs [--big]` | every screen at 1280 / 1440 / 1920; `--big` with 124 lights |
| `vischeck.mjs` | 124 real lights driven through the screen: DMX, 3D targets and the drawn models agree |
| `brands.py [--brand X / --product "words"] --check` | top 20 brands x 15 products (effects machines first): sweep + "armed only", even with channels programmed |
| `vischeck.mjs --brands / --brand X / --product "words"` | the same through the screen, brand by brand, with the effects in 3D |
| `oddcheck_ui.mjs` | two browsers at once; the server dropping out and back |
| `frametiming.py` | steady 40 Hz with a big rig and three screens (CI) |

The browser checks use software rendering here: with 124 lights the 3D is
slow, so they set the 3D to Fast.  Never run two browser checks at once on
one machine - the page never settles and every click times out.

Windows' clock ticks every 16 ms: anything timed by `time.monotonic()`
can see "no time passed".  `coarse.py`-style runs (monkeypatch
`time.monotonic` to 1/64 s steps) reproduce Windows CI failures on Linux.

## Done (PRs #48-#52, then the brand debug)

1. **Screens** (#48): the top bar fits at any width (labels fold into
   icons only when needed); bigger click targets; Esc in Arrange closes a
   menu first; Copilot "Forget this" fixed.
2. **Odd inputs** (#49): the checks above, and the bugs they found - a
   broken autosave stopped the desk starting; a damaged show broke every
   frame; lights from the built-in list came back dead after a restart;
   channel names ("Textured" read as red); CMY LEDs on RGB lights;
   "Failed to fetch" wording; frame time on 520 lights.  CI was red on
   main before the session (Windows clock: glide, beat, tempo tap, shared
   look; Ubuntu: "Find nodes" waited 6 s sending polls) - all fixed.
3. **Real-light checklist and backlog A8** (#50):
   `docs/REAL_LIGHT_CHECKLIST.md`, `docs/BACKLOG.md` A8.
4. **Redesign** (#51): "graphite" look (neutral greys, indigo for
   selection, red live, amber blind, green go, rose AI), sentence-case
   labels, one-row programmer tabs with icons, a wider fixture list,
   grouped top bar with a new logo, Settings in sections, readable
   bottom-panel switch, neutral 3D room light, reduced-motion support.
5. **Debug with 124 lights** (#52): Blackout left lights whose only "off"
   is a colour channel's "Blackout" slot shining (a Swarm) - fixed on the
   wire and in 3D; Full / Out / Locate drive that slot; lights with all
   colours at 0 no longer show lit; a colour channel described only by
   ranges is read as slots; the libsweep has a Blackout check (0 fail).

6. **Brand-by-brand debug** (top 20 brands x 15 products, effects
   machines first; `tools/brands.py`, `vischeck.mjs --brands`, skill
   `fixture-debug`):
   - **Safety:** six lasers switched by a colour / mode channel lit while
     disarmed (Laserworld RS400G...) - held off unless armed and fired;
     MagicFX StadiumBlaster / Blower / Shot III and SwirlFan II were plain
     lights (confetti without ARM) - confetti machines now.
   - **Effects:** fazer / Robe Fog 1500 "Volume control" is the fog output;
     Showtec Dragon F-350 is a hazer, not a flame; a moving fogger is not
     drawn as an always-lit lamp.
   - **Programmer fit:** the colour picker only for lights that mix any
     colour; others get their own colours as buttons and a slider per
     channel.
   - **3D fit:** the body follows the library type (scanners, flowers,
     bars, strobes: 306 products); tilting bars move (Robe Tetra); pixel
     bars, matrices, blinders and moving bars colour each cell
     (`tools/pixcheck.mjs`).
   - Result: 279 products, engine check 0 failures; screen check clean
     except two lights whose "off" the file can't tell (BACKLOG A8/10).

## Left to do

- The operator runs `docs/REAL_LIGHT_CHECKLIST.md` with their node and
  sends notes; each note becomes a fix.
- `docs/BACKLOG.md` A8 (library leftovers) and A7/11 (output extras).
- The ui-ux-pro-max skill's search data is in the repo now (operator's OK,
  2026-10-02): `python .claude/skills/ui-ux-pro-max/scripts/search.py ...`.
- 72 old lamp scanners can't be closed from DMX at all; the 3D shows them
  lit in Blackout (true to the real light).  A notice in the Ready? check
  would warn about them.
