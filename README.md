# Jarvis — Lighting Console

A lighting desk that is your own software, in one window: it patches the rig
from a real fixture library, aims it, programs cues with real fades, and puts
the show on the wire over Art-Net or sACN. There is an AI panel inside it —
a plain-text compiler and show-from-a-prompt, on an allowlist that keeps it
away from arming your output — but the desk does not need it and never asks.

No pip installs, no build step — pure Python standard library + vanilla JS.

```
 console.html  ──►  Engine  ──►  Output thread (40 Hz, Art-Net / sACN)
   │                 │                        │
   │                 │                        ▼
   │                 │                  node ─► DMX512 ─► fixtures
   │                 │
   ├─ patch · programmer · cues · playbacks
   ├─ command line  (1-4 pan 90)          ──┐
   └─ AI panel     (plain text, allowlist) ──┴─► the SAME engine actions
```

**One engine, one output path.** Jarvis *is* the console — there is no second
front end to switch to, no external desk to configure, and no mode to be in.
Every control, the command line and the AI panel all call the same engine
actions, so undo, the lock and dry run mean the same thing whichever one you
used. That is also why the AI is on an allowlist: it reaches the engine the
only way, so what it may reach is a short list.

## Quick start

1. Double-click **`run.bat`** (creates `.env` on first run and opens the app).
   Or run `python app\main.py` and open <http://localhost:8787>.
2. Add your AI key: edit **`jarvis/.env`** — the verified setup is the
   **Gemini free tier** (create a key at <https://aistudio.google.com/apikey>):
   ```
   LLM_API_KEY=AQ....                             # your AI Studio key
   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
   LLM_MODEL=gemini-3.5-flash-lite                # chat + tools + vision, tested
   ```
   Any OpenAI-compatible API works (OpenAI, OpenRouter, Groq, … or a local
   Ollama/LM Studio server via `LLM_BASE_URL`; the model must support tool
   calls *and* images). Without a key the app runs in offline mode: fixture
   search + manual console buttons still work.
3. Import fixture profiles (see below).
4. Press **GO LIVE** when you are ready. Until
   `CONSOLE_DRY_RUN=false` every frame is built and counted but **nothing
   leaves your machine**.

### First run

The app opens on the console, and that is the whole app — `/` serves it.
Press **?** anywhere for the help overlay, built from the live state and
from the engine's own command syntax, so it cannot drift out of date with
the console.

## The workflow it was built for

1. **Add heads** — search the GDTF library (or the Share, signed in), pick the
   DMX mode, and the console auto-addresses each head with no overlap and no
   512-straddle. The patch list is the document the rig actually has, and it
   exports to CSV.
2. **Aim them** — the pan/tilt pad, the **aim** fields in degrees (through each
   fixture's *own* travel, not an abstract 0–255), or the command line:
   `1-4 pan 90`. `align`, `distribute` and `mirror` for a bar of heads.
3. **Build the look** — the attribute grid, the colour picker, palettes per
   family, or type it: `1-8 red 60`, `all dimmer 40`, `cue 3 go`.
4. **Record and run it** — record cues onto a playback, GO / Release, fades and
   follow all interpolated at 40 Hz.
5. **Save the show**, and keep working. The command line, the AI panel and
   every button go through the same engine action, so undo, the lock and dry
   run mean the same thing whichever one you used.

## The console (`/` — the whole app)

The **🎛 Console** button in the top bar opens the desk. It is the whole
package, not a side panel:

* **Patch bay** — add / remove / re-address heads, auto-patch, groups. The
  add-heads dialog lists the **entire fixture library on the side** and the
  search box filters it locally as you type, so "which fixtures do I have?"
  is answerable and picking a half-remembered type is not guesswork.
* **Programmer** - intensity, colour (hex + swatches + an HSV picker ring with a
  saturation/brightness square, which also says how many of the selected heads
  the colour will actually reach), pan/tilt pad, Locate,
  Clear, grand master, blackout.
* **Palettes and cues** — position / colour / beam palettes, cue stacks on 10
  playbacks with real fades, auto-follow, GO / back / forward / release.
* **An editable 3D rig.** Not a preview: you can **drag a light** to where it
  actually hangs (shift+drag for height; crossing 2 m makes it a truss
  fixture), **click a light** to select and highlight it in the patch list,
  and **draw the venue** — the floor plate, grid, walls and truss lines are
  all rendered from what you stored, so the beams land on something real.
  Fixture bodies are shaped by brand and channel layout: a yoke-and-base head
  with a tilted lens for anything with pan/tilt, a long batten for bars and
  beams, a flat panel for washes, a can for PARs.
* **Fades that animate.** A dedicated 20 Hz light feed carries the heads that
  are actually emitting and the renderer eases between ticks, so a 4-second
  cue fade is a ramp rather than ten steps — and because those values come
  from the same merge as the wire, what you see is what goes out.
* **Status bar** — frames sent, **which node they are aimed at**, Hz, drift, sender
  errors, how long ago the server was last heard, and a warning when a cue stack still
  points at heads that are no longer patched (the "playback does nothing" case, made
  visible). A lost server or a failing sender is a persistent banner, not a toast that
  vanishes.
* **A camera you can actually fly.** Right- or middle-drag pans, the wheel zooms toward
  the cursor, <kbd>WASD</kbd>/<kbd>QE</kbd> free-fly, <kbd>1</kbd>–<kbd>5</kbd> jump to
  front / house-left / house-right / back-of-house / overhead, <kbd>F</kbd> frames the
  selection, and two fingers pinch on a tablet. All of it is also on-screen buttons,
  because a gesture nobody can discover is not a feature. The view **keeps your
  viewpoint**: moving a light no longer snaps the camera back to the middle of the room.
  <kbd>⛶ FULL</kbd> gives the view the whole screen when you are hanging a rig, and
  <kbd>Esc</kbd> brings it back. It does not depend on the browser's full-screen API, so it cannot be refused.
* **Select one light, or several.** In the patch list and in the 3D view the same three
  gestures work: **click** selects one, **ctrl/cmd+click** adds or removes it,
  **shift+click** takes everything between. Scattered heads are a normal thing to program, so
  adding a single light to the selection is the common case, not a range.
* **Click a light to select it.** In the 3D view a picked light gets a dashed reticle and
  a label saying which one it is — `#17  CHAUVET DJ Intimidator Spot 260` — so you know
  what you are about to edit. Shift-click adds to the selection, and the highlight is the
  same state as the patch list, not a second opinion.
* **Controls are live while you drag them.** The pan/tilt pad, intensity, grand master
  and the playback faders all reach the desk *during* the drag, not when you let go — so
  the head turns under your finger and the light follows the fader instead of jumping at
  the end. One truss bar per depth, so two lights hung at slightly different heights
  share one bar rather than getting a second one stacked above them.
* **Master and BLACKOUT are pinned** to the bottom of their column, and a sticky
  `NOW PLAYING` strip always shows the running cue and what GO will do next.
* **Moving heads move.** A head with pan/tilt channels is aimed by its real channels, so
  panning and tilting it moves the beam *and* turns the head in the yoke. The aim is
  0–1 turns on the light feed, present only when the fixture has the channel and
  something is driving it — a static PAR keeps its geometric aim.

## Keyboard

A lighting desk is run from the keyboard. Press <kbd>⌨</kbd> in the header for the live
list; every binding is also in its control's tooltip.

| key | does | key | does |
|---|---|---|---|
| <kbd>Space</kbd> / <kbd>Enter</kbd> | GO on the active playback | <kbd>↑</kbd> <kbd>↓</kbd> | intensity ±1 (<kbd>Shift</kbd> ±10) |
| <kbd>B</kbd> · <kbd>]</kbd> · <kbd>[</kbd> | cue back · forward · back | <kbd>X</kbd> | toggle BLACKOUT |
| <kbd>M</kbd> | master 100% / 0% | <kbd>C</kbd> | clear the programmer |
| <kbd>1</kbd>–<kbd>0</kbd> | select head 1–10 | <kbd>L</kbd> | locate the selection |
| <kbd>A</kbd> / <kbd>Shift</kbd>+<kbd>A</kbd> | select all / clear selection | <kbd>R</kbd> | record a cue |
| <kbd>G</kbd> | group the selection | <kbd>F</kbd> | frame the selection in 3D |

In the 3D view (click it once to give it focus): <kbd>WASD</kbd>/<kbd>QE</kbd> fly,
<kbd>↑↓←→</kbd> orbit, <kbd>1</kbd>–<kbd>5</kbd> named views, <kbd>0</kbd> reset.
Typing in a field never fires the rig.

## Connecting a rig (Art-Net / sACN)

1. `DMX_HOST=255.255.255.255` broadcasts (the default). Set it to your node's
   IP for unicast. `DMX_TRANSPORT=artnet` or `sacn`.
2. Same LAN, and Windows firewall must allow Python on private networks.
3. **Scan** in the console header to see which nodes answered, then
   **auto-patch** the universes that showed up.
4. Press **GO LIVE**.

**Verify before touching hardware:**

```
python tools/artnet_loopback.py   # local receiver: bytes, sequence, 40 Hz
```

then an Art-Net viewer / Wireshark, then one cheap fixture on a node, then
the rig. Full spec: `docs/CONSOLE_DESIGN.md`.

### Safety chain

`CONSOLE_DRY_RUN=true` (default) builds and counts frames but nothing leaves
the machine. With `CONSOLE_DRY_RUN=false`, arming still needs an explicit
**GO LIVE** press, the gate is session-only — a restart comes back dry — and
`DMX_BLACKOUT_ON_EXIT=true` makes the rig go dark when the app closes.

`CONSOLE_TOKEN` becomes **required** as soon as `HOST` is anything other than
`127.0.0.1`: `/api/console` can start and stop real DMX, so anyone on the
network could drive your rig. The console prompts for the token and
remembers it.

## Fixture library (the "every brand is in it" part)

The [GDTF Share](https://gdtf-share.com) is the open, manufacturer-fed fixture
database (free account). Download `.gdtf` files — or use the site's
"download entire library" option — drop them into:

```
jarvis/fixtures_inbox/
```

then click **Import GDTF files** in the sidebar, or run:

```
python tools/import_gdtf.py
```

Files are parsed locally (ZIP + XML): manufacturer, model, every DMX mode,
channel counts and per-channel attributes (16-bit channels included). A
corrupt file is reported as an error with its filename rather than quietly
importing nothing.

## Show from a prompt (the AI, inside the console)

The **AI** button in the console's top bar opens a slide-over panel. It is
not a chat window — it is a console tool, and it is worth being precise
about what that means.

**The plain-text compiler.** Type an instruction — *everything to 70%*,
*rainbow across the rig*, *group 2 pulse* — and it compiles to validated
steps and runs them against your selection, through the same engine action
the buttons use. Chips do the same thing instantly. Tick **offline
compiler** to skip the cloud model and use a deterministic keyword
compiler instead, so the panel works with no key and no network.

**The allowlist is the load-bearing part.** The model cannot arm your
output, save or load a show file, import anything, bulk-replace the patch,
or delete a head. Those stay on your own buttons, behind a confirm. Anything
that *is* allowed is non-destructive. So the AI can help you build a look
and cannot switch the rig on.

**Show from a prompt.** The lower half of the panel takes a short brief — *a
winter wedding, slow and elegant, warm whites, avoid red* — and returns
**2–3 genuinely different concepts**: different palettes, cue lists and
fade times. The stage it draws them on is **your rig**: the heads you have
patched, at the positions you hung them, and the panel says so. With nothing
patched it falls back to a synthetic stage and says that too.

Nothing touches the rig until you press **load show**, which asks first. The
loaded cues land on playback 1 and stay there until you press GO. If the
concept's design roles do not intersect your patch's roles, every head
participates rather than nothing, and it tells you the roles it wanted and
the head numbers you have.

## What is not here

This used to be two applications: a chat assistant at `/` and the console
at `/console.html`. There is one now — the console — and five features
that lived only on the assistant page went with it: free chat, the show
audit (*read my show, what would you improve?*), the show builder, the rig
studio, and voice/photo input. The AI itself stayed, as a panel inside the
console (see above).

They could not simply have moved, because each reached the engine by a path
that bypassed the console's own undo, lock and dry-run story. The show
builder is the clearest case: it wrote a patch straight through, so a colour
the desk had applied could not be undone **from the desk that applied it**.
That is the same reason the AI is on an allowlist, and the reason the
allowlist exists at all.

`/index.html` still resolves, and serves the console — so an old bookmark
lands somewhere useful rather than a 404.

## Configuration reference (`jarvis/.env`)

| Key | Default | Meaning |
|---|---|---|
| `APP_NAME` / `APP_SUBTITLE` / `APP_LOGO` | JARVIS / Lighting Assistant / dog.svg | your branding (the UI is a text wordmark; `APP_LOGO` is still served as `/<file>`) |
| `HOST` / `PORT` | 127.0.0.1 / 8787 | bind address and web port |
| `LLM_API_KEY` | *(empty)* | empty = offline mode (Gemini keys start with `AQ.`) |
| `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` | any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | must support tool calls **and** images |
| `CONSOLE_DRY_RUN` | true | `false` = Art-Net/sACN frames may actually hit the network |
| `CONSOLE_TOKEN` | *(empty)* | required once `HOST` is not loopback; sent as `X-Jarvis-Token` |
| `DMX_TRANSPORT` | artnet | `artnet` or `sacn` (E1.31) |
| `DMX_HOST` | 255.255.255.255 | broadcast, or the unicast IP of your Art-Net node |
| `DMX_PORT` / `DMX_HZ` / `DMX_NET` | 6454 / 40 / 0 | port, frame rate (10-120), Net (0-127) |
| `DMX_BLACKOUT_ON_EXIT` | false | `true` = blackout frames on shutdown |
| `CONSOLE_SHOW_DIR` | data/shows | where console show files (save/load) live |
| `SHOW_FOLDER` | *(empty)* | optional extra folder exported patch CSVs are copied into |
| `DMX_INPUT` | false | receive Art-Net/sACN as observable input state |
| `MIDI_ENABLED` | true | MIDI input via winmm; degrades gracefully with no device |

## Tests

```
python tools/selftest.py
```

**1678 checks across 46 suites, 0 failures** — GDTF parser (incl. 16-bit channels), database
search, the auto-patcher (DMX maths, universe rollover, CSV patch import),
show-from-a-prompt staged on your own rig, the colour picker (its HSV maths
*executed* from the shipped source under node, and every picked colour read
out of the real 512-slot frame), the console itself: Art-Net packet bytes,
channel-role mapping, the **merge core** tested standalone (HTP/LTP
precedence, blackout/master ordering, 16-bit pairs, curve application, frame
bytes), the engine (patch, programmer, cues, fades, show files, autosave),
**performance ceilings** on the 40 Hz budget, `set_place`/`set_venue`/
`look_feed`, moving-head aim (8-bit and 16-bit pan/tilt normalisation,
absent-when-not-driven, and the per-fixture tilt travel the 3D view applies),
unicast rig discovery against a real Art-Net node on a real socket, a
`node --check` syntax gate on every web script, a check that the served assets
are revalidated rather than cached, and a `console only` suite that holds the
line: the assistant page and its five features stay gone, the AI stays inside
the console, and the top-level overlays are not nested inside a hidden modal.

Each suite runs isolated: one exception is reported as a failure and the rest
of the run continues, so a single broken test can never hide the other 1400
checks behind it. Run it after any change; `python tools/artnet_loopback.py`
additionally proves the real UDP path.

## Project layout

```
jarvis/
├── run.bat              one-click launcher
├── .env.example         configuration template
├── docs/
│   └── CONSOLE_DESIGN.md the console spec (engine, Art-Net, phases, status)
├── app/
│   ├── main.py          HTTP server + routes + token gate (stdlib only)
│   ├── llm.py           OpenAI-compatible client
│   ├── fixtures.py      SQLite fixture DB + GDTF parser + cache invalidation
│   ├── engine.py        console state: patch/programmer/cues/threads/actions
│   ├── engine_support.py  shared DMX vocabulary, 16-bit maths, curves (pure)
│   ├── merge.py         the 40 Hz core: resolve + frame building (pure)
│   ├── artnet.py        ArtDmx packet builder + UDP sender (Art-Net 4)
│   ├── sacn.py          E1.31 packet builder + UDP sender
│   ├── dmxin.py         Art-Net/sACN input listener
│   ├── midi.py          winmm input + note/CC mapping
│   ├── profiles.py      data-only fixture definition library
│   ├── fx.py            effect/wave engine
│   ├── console_ai.py    AI console compiler (plain text → steps)
│   ├── showdesign.py    prompt -> concepts, palettes, staged on your rig
│   └── config.py        .env loading, branding, logo resolution
├── web/                 console.html + console.js + console.css
│                        + style.css + viz.js (no build step, no CDN)
├── tools/
│   ├── selftest.py      run after changes (1678 checks, 46 suites)
│   ├── artnet_loopback.py  proves the Art-Net packet path end to end
│   └── import_gdtf.py   bulk fixture import
├── fixtures_inbox/      drop .gdtf files here
└── data/fixtures.db     your fixture database (auto-created)
```

## Troubleshooting

* **The AI panel says offline** — no `LLM_API_KEY` in `.env`. Tick *offline
  compiler* to use the deterministic one, which needs no key at all.
* **"unauthorised"** — `CONSOLE_TOKEN` is set (or `HOST` is not loopback with no
  token). The console prompts for it; check it matches `.env`.
* **GO LIVE greyed or refused** — `CONSOLE_DRY_RUN=false` requires the explicit
  confirm press, and the gate is session-only. A restart comes back dry.
* **Nothing on the rig but the app says frames are sent** — check the output
  pill reads `OK` not `STALLED`, run a **scan** (did any node answer?), and
  confirm `DMX_HOST` matches your node (broadcast needs a permit in many
  networks — use unicast).
* **A playback does nothing** — look for the footer warning naming unpatched
  heads. If a saved cue references heads you removed, re-record or re-import.

## Next steps (not built yet)

* **Hardware validation** — no physical Art-Net node has been reachable from the
  development machine, so nothing here is verified against real fixtures. First
  contact is one cheap fixture on a node.
* **Show management** — browse, rename, duplicate and delete `data/shows`. The load
  dropdown lists them and nothing else does, so "which of these seven files is the
  show?" is a real question at load-in.
* **OSC** — the only missing protocol with a clear spec; MIDI already exists.
* MIDI clock / tap tempo — the follow system is there and works; a clock on top is small.
* MIDI or physical fader surfaces beyond the note/CC map.
* Packaging as a real desktop app (Tauri/Electron wrapper → your own `.exe`).
* `app/engine.py` is still one large module; patch management is the next
  honest extraction boundary.
