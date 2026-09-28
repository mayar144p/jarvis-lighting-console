# Jarvis — Lighting Console

A lighting desk in a browser window. It patches the rig from a real fixture
library, shows it in a live 3D visualiser with a model of every light, programs
cues with real fades, and puts the show on the wire over Art-Net or sACN. An
AI copilot sits inside it: plain English in, a previewed plan of desk actions
out, applied as one undoable step. The desk never needs the AI.

No pip installs and no build step: a Python standard-library engine and a
vanilla ES-module front end, with three.js vendored so it runs fully offline.

```
 browser (web/)                           Python engine (app/)
 ┌───────────────────────────┐   POST    ┌──────────────────────────────┐
 │ fixtures · 3D stage ·     │ ────────► │ Engine.act(action, params)   │
 │ programmer · playbacks ·  │           │  undo · lock · dry run       │
 │ command bar · copilot     │ ◄──────── │ merge (HTP/LTP, fades, FX)   │
 └───────────────────────────┘    SSE    └──────────────┬───────────────┘
                                 stream                 │ 40 Hz
                                                        ▼
                                   Art-Net / sACN ─► node ─► DMX512 ─► fixtures
```

Every button, the command bar and the copilot call the same engine actions, so
undo, the patch lock and dry run mean the same thing whichever you used.

## Quick start

1. **Windows:** double-click `run.bat`. **Mac/Linux:** `./run.sh`.
   Both create `.env` from `.env.example` on first run and open
   <http://localhost:8787>. Or run `python app/main.py` yourself.
2. Optional: put an AI key in `.env` (the free Gemini tier works; get a key at
   <https://aistudio.google.com/apikey>):
   ```
   LLM_API_KEY=...
   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
   LLM_MODEL=gemini-3.5-flash-lite
   ```
   Any OpenAI-compatible endpoint with tool calling works: OpenAI, OpenRouter,
   Groq, or a local Ollama / LM Studio. Without a key the copilot uses its
   offline compiler.
3. Add fixtures (below), build a look, record cues.
4. When the rig is connected, set `CONSOLE_DRY_RUN=false` and press **GO
   LIVE**. Until then every frame is built and counted but nothing leaves the
   machine.

Python 3.10+ is required. Node is only needed to run the full test suite.

## The desk

| Region | What it does |
|---|---|
| **Top bar** | show name, output state and **GO LIVE**, undo/redo with the last action named, command palette (<kbd>Ctrl</kbd>+<kbd>K</kbd>), copilot, patch lock, settings, help |
| **Fixtures** (left) | the patch as a table; filter, group chips, add heads, select by click / ctrl-click / shift-click, select similar |
| **Stage** (centre) | the 3D visualiser; named views, frame selection, haze, performers, full screen, and a *now playing* strip |
| **Programmer** (right) | tabs for Level, Colour (HSV picker, swatches, hex, and how many selected heads the colour can reach), Position (pan/tilt pad and aim in degrees), Beam, FX, Looks (palettes and presets), Tools (fan, arrange, limits) |
| **Playbacks** (bottom) | cue stacks with GO / back / release, faders, grand master, BLACKOUT |
| **Status bar** | output target and rate, network, feed health, last save |

It adapts to a 1024 px tablet and to a phone, where the regions become tabs.

### The visualiser

Add a fixture and its model appears on the stage immediately, before a cue
exists and even while the output is dry. Each light is built for what it
physically is and who made it:

* **Type** comes from the fixture's channels and name (`app/fixture_kind.py`):
  moving spot, moving wash, beam, profile, PAR, batten/bar, pixel bar, blinder,
  strobe, fresnel, followspot, laser, hazer, and more, each with its own
  procedural body: yoke and head, can, panel, batten, cells.
* **Brand** (around 30 manufacturers recognised) sets the body colour, finish
  and a badge on the housing.
* **GDTF geometry.** When the profile came from a GDTF file with models, the
  real meshes (3DS, GLB, STL, OBJ) replace the procedural body, with the yoke
  and head as the file's own pivots.
* **Light.** Beams are volumetric cones driven by the live output: colour,
  intensity, zoom, pan/tilt, gobo and strobe, lighting the floor, the flown
  truss and the performers, with bloom and optional haze.
* The fixture picker shows the same 3D model before you patch anything.

Drag a light to reposition it (shift-drag for height). Views:
<kbd>1</kbd>–<kbd>5</kbd> (front, house left, house right, back, plan),
<kbd>F</kbd> to frame the selection.

### Keyboard

| key | does | key | does |
|---|---|---|---|
| <kbd>Space</kbd> / <kbd>Enter</kbd> | GO on the focused playback | <kbd>B</kbd> | cue back |
| <kbd>X</kbd> | toggle BLACKOUT | <kbd>C</kbd> | clear the programmer |
| <kbd>A</kbd> / <kbd>Shift</kbd>+<kbd>A</kbd> | select all / none | <kbd>L</kbd> | locate |
| <kbd>1</kbd>–<kbd>9</kbd> | select head (<kbd>Shift</kbd> adds) | <kbd>↑</kbd> <kbd>↓</kbd> | intensity ±5 (<kbd>Shift</kbd> ±1) |
| <kbd>R</kbd> | record a cue | <kbd>G</kbd> | group the selection |
| <kbd>F</kbd> | frame the selection | <kbd>/</kbd> or <kbd>Ctrl</kbd>+<kbd>K</kbd> | command bar |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd> | undo / redo | <kbd>Ctrl</kbd>+<kbd>S</kbd> | save show |
| <kbd>?</kbd> | help | <kbd>Esc</kbd> | close the top panel |

Typing in a field never fires the rig.

### Command bar

<kbd>/</kbd> opens it. It searches every action and also takes desk syntax:
`1-4 red`, `1-8 at 60`, `all dimmer 40`, `1-4 pan 90`, `cue 3 go`.

## The copilot

**AI** in the top bar opens it. Three tabs:

* **Program.** *“Warm wash on the pars at 70%, then a slow rainbow on the
  movers.”* The request, your rig (types, groups, palettes, cues, selection)
  and recent turns go to the model, which returns a structured plan through a
  forced tool call. You see the steps first, then apply them. They run as
  **one** undo step labelled *copilot*, and if any step fails the whole plan
  rolls back. Without a key (or with *offline* ticked) a keyword compiler
  handles common requests: levels, colours, named effects (rainbow, circle,
  figure eight, pan sweep, breathe, dimmer chase, sparks), aim, beam, fades,
  and targets like *movers* or *pars*.
* **Design a show.** A brief (*winter wedding, slow and elegant, warm whites, avoid
  red*) returns two or three different concepts, each with a palette and cue
  list, staged on your patched rig. Nothing changes until you load one.
* **Diagnose.** The show doctor reads the show and reports problems with a fix where one exists:
  nothing patched, universes seen on the network but not patched, guessed
  profiles, channels with no control, cues pointing at removed heads, lights
  piled at one spot, output blind or stalled, a broadcast target that cannot
  reach the node.

**The allowlist.** The model can only call a fixed list of non-destructive
actions with validated parameters. It cannot arm output, load or save show
files, import, replace the patch or delete heads; those stay on your own
buttons.

## Connecting a rig (Art-Net / sACN)

1. Defaults: Art-Net goes to the directed broadcast of your lighting subnet
   (for example `2.255.255.255` on a 2.x network, `192.168.1.255` on a
   192.168.1.x one). sACN goes to each universe's multicast group
   (`239.255.x.y`). Set `DMX_HOST` to a node's IP for unicast;
   `DMX_TRANSPORT=artnet` or `sacn`.
2. Same LAN, and the OS firewall must allow Python on private networks.
3. **Scan** in settings to see which nodes answered, then auto-patch the
   universes they reported.
4. Press **GO LIVE**.

Verify before touching hardware:

```
python tools/artnet_loopback.py   # local receiver: bytes, sequence, 40 Hz
```

then an Art-Net viewer or Wireshark, then one cheap fixture, then the rig.

### Safety

* `CONSOLE_DRY_RUN=true` (default): frames are built and counted, never sent.
* With it `false`, output still needs an explicit **GO LIVE**, and that is
  per session: a restart comes back dry.
* `DMX_BLACKOUT_ON_EXIT=true` sends blackout frames when the app closes.
* `CONSOLE_TOKEN` is **required** once `HOST` is not loopback. The browser
  asks for it and keeps it for that tab only.
* Every API write must be same-origin `application/json`, and on a loopback
  bind the `Host` header must be a loopback name. That blocks cross-site
  requests and DNS rebinding from other web pages.

## Fixture library

The [GDTF Share](https://gdtf-share.com) is the open, manufacturer-fed fixture
database (free account). Either:

* sign in from the add-fixtures dialog and search and download in the app, or
* drop `.gdtf` files into `fixtures_inbox/` and press **Import**, or run
  `python tools/import_gdtf.py`.

Files are parsed locally: every DMX mode, 16-bit channels, ranges and
geometry. A corrupt file is reported by name. Downloaded files stay on your
machine and are never committed (`data/` and `*.gdtf` are git-ignored).

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `APP_NAME` / `APP_SUBTITLE` | JARVIS / Lighting Assistant | branding |
| `HOST` / `PORT` | 127.0.0.1 / 8787 | bind address and web port |
| `CONSOLE_TOKEN` | *(empty)* | required when `HOST` is not loopback; sent as `X-Jarvis-Token` |
| `LLM_API_KEY` | *(empty)* | empty: the copilot runs offline |
| `LLM_BASE_URL` | Gemini's OpenAI endpoint | any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | must support tool calls |
| `LLM_TIMEOUT` | 45 | seconds per model call |
| `CONSOLE_DRY_RUN` | true | `false` lets frames reach the network (after GO LIVE) |
| `DMX_TRANSPORT` | artnet | `artnet` or `sacn` |
| `DMX_HOST` | *(auto)* | Art-Net: subnet directed broadcast; sACN: `multicast`; or a unicast IP |
| `DMX_PORT` / `DMX_HZ` / `DMX_NET` | 6454 / 40 / 0 | port, frame rate (10–120), Art-Net Net (0–127) |
| `DMX_BLACKOUT_ON_EXIT` | false | blackout on shutdown |
| `SACN_PRIORITY` / `SACN_SOURCE_NAME` | 100 / APP_NAME | E1.31 source fields |
| `DMX_INPUT` | false | listen for Art-Net/sACN input |
| `MIDI_ENABLED` / `MIDI_DEVICE` / `MIDI_MAP` | true / first / data/midi_map.json | MIDI input (Windows); degrades quietly with no device |
| `CONSOLE_SHOW_DIR` | data/shows | show files |
| `CONSOLE_AUTOSAVE` / `CONSOLE_AUTORESTORE` | true / true | autosave the desk and restore it on start |
| `FIXTURE_DB` | data/fixtures.db | fixture database |
| `FIXTURE_SEED_BUILTINS` | false | seed generic profiles (their channel maps are guesses) |
| `GDTF_SHARE_USER` / `GDTF_SHARE_PASSWORD` | *(empty)* | optional; you can also sign in from the app |

## Tests

```
python tools/selftest.py
```

About 1,600 checks: GDTF parsing and geometry, the fixture database, the
auto-patcher, the merge core (HTP/LTP, master and blackout order, 16-bit,
curves), the engine (patch, programmer, fades, cues, undo, batches, show files,
autosave), 40 Hz performance ceilings, Art-Net and sACN packet bytes, discovery
against a real socket, HTTP security (token, origin, content type, host), the
live stream, fixture type and brand recognition, the doctor, the copilot
(allowlist, validation, offline compiler, atomic apply), and the web app (every
module imports, element ids exist, colour picker maths and the 3DS parser
executed under node). Suites run isolated, so one crash cannot hide the rest.

CI (`.github/workflows/ci.yml`) runs it on Linux and Windows, plus a lint pass.

## Project layout

```
├── run.bat / run.sh       launchers (Windows / Mac & Linux)
├── .env.example           configuration template
├── docs/ARCHITECTURE.md   how the pieces fit together
├── app/
│   ├── main.py            HTTP server, routes, auth, live stream
│   ├── engine.py          desk state and every action: patch, programmer, cues, undo
│   ├── engine_support.py  DMX vocabulary, 16-bit maths, curves (pure)
│   ├── merge.py           the 40 Hz core: resolve and build frames (pure)
│   ├── fx.py, fxlib.py    waveform engine and named effects
│   ├── artnet.py, sacn.py output senders and discovery
│   ├── dmxin.py, midi.py  inputs
│   ├── fixtures.py        SQLite fixture library and GDTF import
│   ├── gdtf_geom.py       GDTF geometry and model extraction for the visualiser
│   ├── gdtfshare.py       GDTF Share client
│   ├── fixture_kind.py    physical type, brand and auto-placement of a fixture
│   ├── profiles.py        built-in generic profiles
│   ├── llm.py             OpenAI-compatible client (tool-call structured output)
│   ├── console_ai.py      copilot: rig context, allowlist, plans, offline compiler
│   ├── showdesign.py      show-from-a-brief concepts
│   ├── doctor.py          show health checks
│   └── config.py          .env loading and defaults
├── web/
│   ├── index.html         the app shell
│   ├── app/               UI modules (store, actions, panels, copilot, keys)
│   ├── js/stage/          visualiser (models, materials, venue, GDTF meshes)
│   └── vendor/three/      three.js, vendored for offline use
├── tools/
│   ├── selftest.py        the test suite
│   ├── featurecheck.py    feature coverage report
│   ├── artnet_loopback.py prove the UDP path locally
│   └── import_gdtf.py     bulk fixture import
├── fixtures_inbox/        drop .gdtf files here
└── data/                  runtime data (git-ignored): fixtures.db, shows, cache
```

## Troubleshooting

* **Copilot says offline:** no `LLM_API_KEY`. The offline compiler still
  handles common requests.
* **"unauthorised":** `CONSOLE_TOKEN` is set, or `HOST` is not loopback. Enter
  the token when asked.
* **403 "unexpected Host header":** you opened the desk by a non-loopback name
  while it is bound to loopback. Use `localhost`, or set `HOST` and a token.
* **Frames counted but nothing on the rig:** open **Diagnose** in the copilot. Then check the
  output reads LIVE, scan for nodes, and set `DMX_HOST` to the node's IP if
  broadcast is blocked on your network.
* **A playback does nothing:** Diagnose lists cues that point at heads no
  longer patched.

## Not built yet

* Validation against physical nodes and fixtures.
* Show file management (rename, duplicate, delete) beyond save and load.
* OSC input, MIDI clock and tap tempo.
* A packaged desktop build.
