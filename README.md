# Jarvis — Lighting Console

A lighting desk in a browser window. It patches the rig from a real fixture
library and shows it in a live 3D model of your venue: the room, the stage,
the truss the lights hang on, and a crowd. It programs cues with real fades,
plays instant quick buttons, runs a timeline against your music, and puts the
show on the wire over Art-Net or sACN. An AI copilot sits inside it. It can
program from plain English, or read the whole rig and build a show onto the
timeline. Every AI change is previewed first and is one undoable step. The
desk never needs the AI.

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

   **Updates are automatic.** Each launch fetches the latest version and
   fast-forwards to it. It's skipped with a one-line note when you're
   offline, have edited Jarvis's own files, or have commits of your own.
   Your shows, fixtures and `.env` are never touched. After an update,
   fixtures you already downloaded are re-read if the importer has
   improved, so there's no need to download them again. Set
   `AUTO_UPDATE=false` in `.env` to turn it off.
2. Optional: the AI copilot. **Settings -> AI**: paste a Gemini key (the
   free tier works; <https://aistudio.google.com/apikey>), and/or download
   the offline AI (desktop app; ~5 GB, unlimited, no internet), and pick
   **Online**, **Local** or **Auto** (Gemini first, the offline AI when
   Gemini hits its limit or there's no internet).  The Windows installer
   has a ticked **Include the offline AI** box: the desk then downloads the
   model that fits the computer in the background the first time it starts
   (progress in Settings -> AI; Pause there stops it), so the copilot works
   offline once it's done.  Or, as before, in `.env`:
   ```
   LLM_API_KEY=...
   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
   LLM_MODEL=gemini-3.5-flash-lite
   ```
   Any OpenAI-compatible endpoint with tool calling works: OpenAI, OpenRouter,
   Groq, or a local Ollama / LM Studio. Without a key the copilot uses its
   offline compiler.
3. Add fixtures (see [the user guide](docs/GUIDE.md#the-desk)), build a look, record cues.
4. When the rig is connected, set `CONSOLE_DRY_RUN=false` and press **GO
   LIVE**. Until then every frame is built and counted but nothing leaves the
   machine.

Python 3.10+ is required. Node is only needed to run the full test suite
and the desktop app.

### The desktop app (testing)

**Windows:** double-click `run-desktop.bat`. **Mac/Linux:** `./run-desktop.sh`.
The first time it fetches Electron (about 100 MB, needs
[Node.js](https://nodejs.org)). It's the same desk, in its own windows:

- **No address, no browser.** The engine starts hidden on a free port that
  only this computer can reach, with a private key made at start-up; the
  app stops it cleanly (show saved) when you close it.
- **Desk → Show mode** (Ctrl+Shift+S) puts each part of the desk in its own
  window: the 3D view (full screen on the second monitor), the programmer
  and fixtures, the playbacks and buttons (a touch screen). Move them
  where you want: each window remembers its monitor and size, and the same
  set opens next time. **Desk → Everything in one window** goes back.
- **Safe during a show:** Ctrl+R, F5 and Ctrl+W do nothing; closing the last
  window asks first; the screens don't sleep while it's open.

Your shows and `.env` are the same as with `run.bat` (it runs the same
`data/` folder).

**The installer (another PC, nothing to install first).** GitHub → Actions →
"Desktop app (installers)" → Run workflow. A few minutes later the
run has `Jarvis-Windows-installer` under Artifacts: unzip it and run
`Jarvis Setup ….exe`. The app carries its own Python. Installed, the shows,
fixtures and settings live in `%APPDATA%\Jarvis` (your AI key goes in
`%APPDATA%\Jarvis\.env`), so they survive updates and reinstalls. The
artifact stays private to the repo and is kept 14 days.

**Another computer, a phone or a tablet:** Desk → Other computers, phones and
tablets… → Allow. The desk then answers your network on a fixed port, locked
with a pairing code. On the other device open the address it shows and type
the code once. A computer gets the **whole desk** - patch, program, the 3D -
working on this computer's show, live on both screens; `/?window=playbacks`
gives just the faders and buttons for a phone. "New code" signs every other
device out; "Stop allowing others" goes back to this computer only. If the
page never loads on the other device, Windows Firewall is blocking it: the
same dialog has **Let it through Windows Firewall** (from your network and
Tailscale only).

**From another town or country** (you in Sweden, the lights in Australia):
don't share the screen - run the desk on the computer with the lights and
open it in your own browser. Both install **Tailscale** (free) and sign in
to the same account; then use the Tailscale address the dialog shows
(100.x.x.x). Only your button presses travel (about 0.15 s Sweden -
Australia), the 3D draws on your own computer, and the fades, effects and
timeline run on the lights' computer, so the light stays smooth. Never open
the port on your router to the internet. Started with `run.bat` instead of
the app: put `HOST=0.0.0.0` and a `CONSOLE_TOKEN=` code of your own in
`.env`.

**USB DMX:** Settings → Output → "USB DMX interface (Enttec USB Pro and
compatible)" and pick its port (COM3...). One box is one universe
(universe 1). Most USB boxes speak this protocol (Enttec DMX USB Pro / Mk2,
DMXking ultraDMX and clones); the cheap "Open DMX" kind does not.
**MIDI** controllers work in the app as in the browser.

**Mac:** the same workflow builds `Jarvis.dmg` (artifact
`Jarvis-Mac-installer`). It isn't signed: the first time, right-click the
app → Open.

**Updates:** `run-desktop.bat` / `.sh` (the app from this repo) brings
itself up to date every time it opens, like `run.bat`: "checking for
updates…", then the newest version (it reopens itself when the app changed).
Same rules as `run.bat`: never with your own edits, never touching your
data, skipped offline or with `AUTO_UPDATE=false`. An installed copy (the
installer) doesn't update itself yet: that needs a place to publish
installers, which waits until Jarvis leaves testing.

## Read on

| Guide | What's in it |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | **The user guide**: the desk, venue, visualiser, quick buttons, timeline, shows, workspaces, keyboard, the copilot and AI, connecting a rig, safety, templates, OSC out, controllers, MVR, the fixture library, lasers |
| [docs/DEVELOPING.md](docs/DEVELOPING.md) | tests, the project layout, the visual checks on your own PC |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the engine and the front end fit together |
| [docs/REAL_LIGHT_CHECKLIST.md](docs/REAL_LIGHT_CHECKLIST.md) | checking the desk against real lights |
| [docs/BACKLOG.md](docs/BACKLOG.md) | what's planned |

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `APP_NAME` / `APP_SUBTITLE` | JARVIS / Lighting Assistant | branding: the name in the title and top bar (the screens' own words say "the desk") |
| `HOST` / `PORT` | 127.0.0.1 / 8787 | bind address and web port |
| `AUTO_UPDATE` | true | `run.bat` / `run.sh` fetch the latest version on launch |
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
| `DMX_SYNC` | true | Art-Net: an ArtSync after each frame so several universes change together (never to a broadcast address) |
| `SACN_SYNC_UNIVERSE` | 0 | sACN: E1.31 synchronisation on this universe (1–63999); 0 = off |
| `DMX_INPUT` | false | listen for Art-Net/sACN input |
| `MIDI_ENABLED` / `MIDI_DEVICE` / `MIDI_MAP` | true / first / data/midi_map.json | MIDI input (Windows); degrades quietly with no device |
| `CONSOLE_SHOW_DIR` | data/shows | show files |
| `CONSOLE_AUTOSAVE` / `CONSOLE_AUTORESTORE` | true / true | autosave the desk and restore it on start |
| `FIXTURE_DB` | data/fixtures.db | fixture database |
| `FIXTURE_SEED_BUILTINS` | false | seed generic profiles (their channel maps are guesses) |
| `CONSOLE_DATA_DIR` | `data/` | move the whole data folder (the tests use a scratch one) |
| `GDTF_SHARE_USER` / `GDTF_SHARE_PASSWORD` | *(empty)* | optional; you can also sign in from the app |

## Troubleshooting

* **Copilot says offline:** no AI yet - Settings -> AI (a Gemini key, or
  the offline AI). The offline compiler still handles common requests.
* **The offline AI's first answer is slow:** it loads the model (up to a
  minute); after 10 idle minutes it stops to free the graphics card.
* **"unauthorised":** `CONSOLE_TOKEN` is set, or `HOST` is not loopback. Enter
  the token when asked.
* **403 "unexpected Host header":** you opened the desk by a non-loopback name
  while it is bound to loopback. Use `localhost`, or set `HOST` and a token.
* **Frames counted but nothing on the rig:** open **Diagnose** in the copilot. Then check the
  output reads LIVE, open **Settings → Output**, pick the node's IP and read the network check.
  It says when the computer isn't on the node's network.
* **A playback does nothing:** Diagnose lists cues that point at heads no
  longer patched.
* **GDTF Share says "not signed in":** your session expired. The Add
  dialog now shows **Sign in** whenever you're not signed in, even with the
  catalogue saved, and a download that needs a session asks you to sign in
  and then retries. With `GDTF_SHARE_USER` and `GDTF_SHARE_PASSWORD` in
  `.env`, Jarvis signs in again by itself.

## Not built yet

* Validation against more physical nodes and fixtures (the light check and
  `docs/REAL_LIGHT_CHECKLIST.md` cover the ones you have).
* Lasers and effects whose fixture file doesn't say which channel fires them
  (13 in the libraries): the desk won't fire what it can't identify - add the
  light from its manual instead.
* Lights whose channels change meaning with a mode channel (Chauvet
  ColorStrip Mini's "Run Speed / Red / Fade Speed"): the desk drives the
  channel as one thing.
* The full list, with what's done: `docs/BACKLOG.md`.

## Reporting a bug

Open the repo's **Issues** tab → **New issue** and pick a form:

- **Problem with a light** - a fixture does the wrong thing on the rig, in
  3D or in the programmer (say which light, which mode, what happened).
- **Something else is wrong** - screens, playbacks, output, saving...
- **Request a fixture** - a light no library has yet (Add fixtures →
  **Request it…** fills it in).

Drag in a screenshot, the light's DMX (its row → ⋯ → Show DMX channels)
and, if you can, the show file so the exact rig can be loaded.  Never
attach `.env`.  Quicker: right-click the light → **Report a problem with
this light…** (or Help → Report a bug) packs all of that for you.

**No GitHub account?** With a report relay set up (`REPORT_RELAY` in
`.env`; how: `tools/report-relay/README.md`), the report window also has
**Send report**: the report is filed for you, with everything attached.

When the desk itself hits a problem - an error in the page, a desk error, a
request that got no answer - a **⚠ badge** appears in the top bar.  Click it
to see what happened, **Report it** (they are attached) or **Dismiss**.
