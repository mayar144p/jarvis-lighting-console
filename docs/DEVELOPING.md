# Jarvis — for developers

Tests, the project layout and the visual checks. Getting it running is in the [README](../README.md).

## Tests

```
python tools/selftest.py
```

About 3,300 checks: GDTF parsing and geometry, the fixture database, the
auto-patcher, the merge core (HTP/LTP, master and blackout order, 16-bit,
curves, quick-button overrides, blackout of dimmer-less lights), the engine
(patch, programmer, fades, cues, undo, batches, show files, autosave), the
venue (templates, rigging, mounting, placement, floor plans), quick buttons,
aim-at-a-spot (each beam recomputed as the 3D view draws it and checked
against the target), the timeline (clock, spans, automation, seek), the
whole-show builder, 40 Hz performance ceilings, Art-Net and sACN packet bytes, discovery
against a real socket, HTTP security (token, origin, content type, host), the
live stream, fixture type and brand recognition, the doctor, the copilot
(allowlist, validation, offline compiler, atomic apply), and the web app (every
module imports, element ids exist, colour picker maths and the 3DS parser
executed under node). Suites run isolated, so one crash cannot hide the rest.

CI (`.github/workflows/ci.yml`) runs it on Linux and Windows, plus a lint pass,
the library rules (`rulecheck.py`: about 2,400 lights against the desk's
promises) and a screen check of the key screens in a real browser.  Every
night (`nightly.yml`) the whole self-test runs three times on Linux and
Windows to catch flaky tests (one that fails once in three is a bug to fix,
not a re-run), the full screen check runs at three widths, and both
installers are built and checked (`desktop.yml`).

Two rules keep the desk from hanging: another program is only ever run
through `app/procs.py` (it stops the program and everything it started when
time is up), and every request from the screen has a time limit
(`web/app/api.js`: 30 s, 5 minutes for AI, imports and downloads).  The
self-test fails any code that breaks either.

The other checks (all in `tools/`, all on scratch data - `data/` is never
touched):

| Check | What it proves | Time |
|---|---|---|
| `rulecheck.py [--only X \| --rule id]` | every library light against the desk's promises (strobe range and direction, colour effects, never grey when lit, every head of a multi-head light); fails only on a new failure (CI) | ~80 s |
| `gigcheck.py` | a whole gig, then save, reload, compare (CI) | <1 s |
| `oddcheck.py` | odd inputs: empty rig, 12,900 nonsense values, 520 lights, corrupt shows and autosaves, a restart, Undo all the way (CI) | ~1 min |
| `libsweep.py [--all-modes]` | every library light (and every mode: 8,093) x Full, Blackout, colour, gobo, move, Locate, effects, SFX - DMX vs 3D | ~3 min |
| `uicheck.mjs [--big]` | every screen and dialog at 1280 / 1440 / 1920: spills, tiny or off-screen controls, page errors; `--big` with the 20-light test rig (`BIG_RIG=1` for all 124) | ~20 min |
| `lookcheck.py [name]` | every channel of the test rig's lights stepped through every range its file lists: the 3D shows what the words say (prism facets and turn, gobo shake / turn / scroll, split colours, strobe kinds) (CI) | <1 s |
| `vischeck.mjs` | 20 real lights (one of each model; `BIG_RIG=1` for 124) programmed through the screen: DMX, the 3D's targets and the drawn models agree for every light | ~5 min |
| `brands.py [--brand X \| --product "words"] --check` | the top 20 brands, 15 products each (effects machines first): libsweep's checks plus "fire / lasers only when armed", even with their channels programmed | ~4 min |
| `vischeck.mjs --brands \| --brand X \| --product "words"` | the same rigs through the screen, one brand at a time, plus the effects: disarmed nothing fires; armed, fire / fog / laser show in the 3D; KILL stops them | ~3 min a brand |
| `oddcheck_ui.mjs` | two browsers on one desk; the server dropping out and coming back | ~1 min |
| `frametiming.py` | DMX on a steady 40 Hz with a big rig and three screens (CI) | 5 s |
| `webgpucheck.mjs` | the 3D on real WebGPU inside the desktop app: it starts, draws with no errors, and draws the same picture as its WebGL 2 fallback (`cd desktop && npm install` first) | ~1 min |
| `fpscheck.mjs` | how smooth the 3D runs with the 20-light test rig (`BIG_RIG=1` for 124) all lit and moving, at High / Medium / Low (`CHECKS_GPU=1` for your graphics card) | ~2 min |
| `desktopcheck.mjs` | the desktop app: engine hidden on a private port and key, Show mode's windows each show their part, Ctrl+R / F5 don't reload, closing stops the engine (`cd desktop && npm install` first) | ~1 min |

The browser checks need Node and Playwright.  `docs/REAL_LIGHT_CHECKLIST.md`
is the 15-minute check with real lights.

## Project layout

```
├── run.bat / run.sh       launchers (Windows / Mac & Linux)
├── .env.example           configuration template
├── docs/ARCHITECTURE.md   how the pieces fit together
├── app/
│   ├── main.py            HTTP server, routes, auth, live stream
│   ├── engine.py          the Engine: desk state, act(), undo, frames, state feeds
│   ├── engine_base.py     the action list, constants and helpers the parts share
│   ├── engine_*.py        the Engine's parts (mixins): patch, select, attrs, rig, quick, fxlayer,
│   │                      move, timeline, program, cues, output, cmdline, looks, shows
│   ├── engine_support.py  DMX vocabulary, 16-bit maths, curves (pure)
│   ├── merge.py           the 40 Hz core: resolve and build frames (pure)
│   ├── fx.py, fxlib.py    waveform engine and named effects
│   ├── artnet.py, sacn.py output senders and discovery
│   ├── dmxin.py, midi.py  inputs
│   ├── fixtures.py        SQLite fixture library and GDTF import
│   ├── gdtf_geom.py       GDTF geometry and model extraction for the visualiser
│   ├── gdtfshare.py       GDTF Share client
│   ├── fixlib.py          Open Fixture Library + QLC+ parsers and search
│   ├── fixlib/            the two libraries, bundled (zips + licences)
│   ├── fixture_kind.py    physical type, brand and auto-placement of a fixture
│   ├── venue.py           the room: templates, rigging, mounts, zones (pure)
│   ├── timeline.py        the show timeline: tracks, clips, automation (pure)
│   ├── autoshow.py        rig analysis → show design → cues + timeline
│   ├── profiles.py        built-in generic profiles
│   ├── llm.py             OpenAI-compatible client (tool-call structured output)
│   ├── console_ai.py      copilot: rig context, allowlist, plans, offline compiler
│   ├── showdesign.py      show-from-a-brief concepts
│   ├── doctor.py          show health checks
│   └── config.py          .env loading and defaults
├── web/
│   ├── index.html         the app shell
│   ├── app/               UI modules (store, actions, panels, copilot, keys,
│   │                      venue panel, quick buttons, timeline)
│   ├── js/stage/          visualiser (models, materials, venue, editor, GDTF meshes)
│   └── vendor/            three.js and pdf.js, vendored for offline use
├── tools/
│   ├── selftest.py        the test suite (runs tools/selftests/)
│   ├── featurecheck.py    feature coverage report
│   ├── artnet_loopback.py prove the UDP path locally
│   ├── import_gdtf.py     bulk fixture import
│   ├── build_fixture_libraries.py  refresh the bundled OFL / QLC+ zips
│   └── update.py          fast-forward update, run by the launchers
├── fixtures_inbox/        drop .gdtf / .qxf / OFL .json files here
└── data/                  runtime data (git-ignored): fixtures.db, shows, cache
```

## Running the visual checks on your own PC (graphics card)

The screen and 3D checks are much faster - and closer to a real show - on
a computer with a graphics card.  Once, in the project folder (PowerShell):

```
npm install playwright
npx playwright install chromium
```

Then, each time (PowerShell; `CHECKS_GPU=1` opens a visible window on the
graphics card - you can watch it work):

```
$env:CHECKS_GPU = "1"
node tools/uicheck.mjs uicheck-out --big          # every screen, 20-light rig
node tools/vischeck.mjs vischeck-out              # 20 lights: DMX, 3D, drawing
node tools/vischeck.mjs --brand Chauvet out-chauvet
node tools/pixcheck.mjs ofl chauvet-dj/colorband-pix.json 36-channel
```

Each writes a `report.md` (and screenshots) in its folder: paste the report
back into the Claude session, or run Claude Code on this PC and it runs and
reads them itself.  Run one check at a time.
