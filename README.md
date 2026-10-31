<p align="center">
  <img src="web/icons/icon.svg" width="112" alt="Haze logo: three beams from one light">
</p>

<h1 align="center">Haze</h1>

<p align="center"><b>A lighting desk for clubs, DJs and live shows.</b><br>
Patch the rig, see it in 3D, program cues and run the show, on one computer or across several screens.</p>

---

## What Haze does

- **Patch from a real fixture library.** Thousands of lights from the
  built-in library, Open Fixture Library, QLC+ and GDTF Share, or a plot
  imported from MVR or CSV.
- **See the show before the lights are on.** A live 3D model of your venue:
  the room, the stage, the truss and the crowd, with every beam as you
  programmed it.
- **Program like a pro desk.** Palettes, groups, cues with real fades,
  delay and fan, effects (in your own colours), drawn movement shapes,
  flip and mirror, and a keypad command line (`1 THRU 4 @ FULL`).
- **Play it live.** Playbacks with GO, quick buttons and faders, a timeline
  that follows the music, tap tempo, Pro DJ Link and Ableton Link, and an
  autopilot that keeps looks changing between songs.
- **Send it to the lights.** Art-Net or sACN to any node, or a USB DMX
  box. MIDI controllers, OSC, a Stream Deck and Philips Hue lamps join in.
- **An AI copilot, if you want one.** Ask in plain English ("slow blue
  sweep on the movers") or let it build a whole show onto the timeline.
  Every change it makes is shown first and undoes in one step. The desk
  never needs it.

Nothing to install for the engine: plain Python (standard library) and a
plain JavaScript front end, with the 3D library included, so Haze runs fully
offline.

## Start in two minutes

**Windows:** double-click `run-desktop.bat`.
**Mac / Linux:** run `./run-desktop.sh`.

The first start downloads the app shell (about 100 MB). After that Haze
opens in its own window, and a **Haze** shortcut appears beside the file:
drag it to the desktop or taskbar.

You need [Python 3.10+](https://www.python.org) and
[Node.js](https://nodejs.org). The installer below needs neither.

Then:

1. **Set up:** add your lights, shape the room, connect the output.
   The Set up screen walks you through it in order.
2. **Program:** select lights, pick palettes, record cues and buttons.
3. **Live:** press GO, play buttons, ride the faders.

Haze starts **blind**: everything is worked out and counted, but nothing
leaves the computer. Press **Go live…** in the top bar when the rig is
connected.

### Install it on another computer

GitHub → **Actions** → **Desktop app (installers)** → **Run workflow**.
A few minutes later the run has **Haze-Windows-installer** (or
**Haze-Mac-installer**) under Artifacts. Unzip it and run the installer:
it carries its own Python, so there's nothing else to install.

Installed, your shows, fixtures and settings live in your own folder
(`%APPDATA%\Haze` on Windows), so they survive updates and reinstalls. The
Mac app isn't signed yet: the first time, right-click it → **Open**.

### Updates

Started from this folder (`run-desktop.bat` / `.sh`), Haze updates itself
every time it opens and reopens if the app changed. It never touches your
shows, fixtures or settings, never overwrites your own edits, and skips the
update when you're offline or `AUTO_UPDATE=false`. Shows saved by earlier
versions open as usual.

## The AI copilot (optional)

**Settings → AI**, then any of:

- **Online:** paste a Gemini key (the free tier works:
  <https://aistudio.google.com/apikey>).
- **Offline:** download the offline AI (about 5 GB, no internet, no daily
  limit). The Windows installer has an **Include the offline AI** tick that
  fetches it in the background on first start.
- **Auto:** online first, offline when the online AI is out of quota or
  there's no internet.

Or put it in `.env`:

```
LLM_API_KEY=...
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-3.5-flash-lite
```

Any OpenAI-compatible service with tool calls works (OpenAI, OpenRouter,
Groq, Ollama, LM Studio). With no AI at all, the copilot still handles
common requests with its own offline rules.

## More screens, more places

**Show mode** (Desk menu, Ctrl+Shift+S) puts each part of the desk in its
own window: the 3D view full screen on a second monitor, the programmer on
one screen, the playbacks on a touch screen. Each window remembers where it
was.

**Phones, tablets and other computers:** Desk → **Other computers, phones
and tablets…** → **Allow**. Open the address it shows on the other device
and type the pairing code once. A computer gets the whole desk;
`/?window=playbacks` gives a phone just the faders and buttons. If the page
never loads, the same dialog can let Haze through Windows Firewall.

**From another country:** run Haze on the computer with the lights and
open it from yours over **Tailscale** (free). Only your button presses
travel; fades, effects and the timeline run next to the lights, so the
light stays smooth. Never open the desk's port on your router to the
internet.

**USB DMX:** Settings → Output → **A USB DMX box**. Enttec DMX USB Pro and
compatible boxes, and Open DMX / FTDI boxes (on Windows, tick "Load VCP" for
the box in Device Manager so it shows up as a COM port). One box is one
universe.

**Safe during a show:** Ctrl+R, F5 and Ctrl+W do nothing, closing the last
window asks first, the screen never sleeps while Haze is open, and stopping
the output takes a deliberate hold.

## How it fits together

```
 Haze window (web/)                        Haze engine (app/)
 ┌───────────────────────────┐   action  ┌──────────────────────────────┐
 │ fixtures · 3D view ·      │ ────────► │ one action at a time         │
 │ programmer · playbacks ·  │           │  undo · lock · blind         │
 │ command line · copilot    │ ◄──────── │ mixing (HTP/LTP, fades, FX)  │
 └───────────────────────────┘   live    └──────────────┬───────────────┘
                                 updates                │ 40 times a second
                                                        ▼
                               Art-Net / sACN / USB ─► node ─► DMX ─► lights
```

Every button, the command line and the copilot use the same engine actions,
so undo, the patch lock and blind work the same whichever you used.

## Read on

| Guide | What's in it |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | **The user guide**: the desk, the venue, the 3D view, buttons, the timeline, shows, keys, songs, the copilot, connecting a rig, safety, OSC, controllers and the Stream Deck, Philips Hue, a backup desk, MVR, the fixture library, lasers |
| [docs/DEVELOPING.md](docs/DEVELOPING.md) | tests, the project layout, the visual checks |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the engine and the screens fit together |
| [docs/REAL_LIGHT_CHECKLIST.md](docs/REAL_LIGHT_CHECKLIST.md) | checking the desk against real lights |
| [docs/BACKLOG.md](docs/BACKLOG.md) | what's planned |

## Settings file (`.env`)

Made from `.env.example` the first time Haze starts. Never share it: it can
hold your AI key.

| Key | Default | Meaning |
|---|---|---|
| `APP_NAME` / `APP_SUBTITLE` | HAZE / Lighting Assistant | the name in the title and top bar |
| `HOST` / `PORT` | 127.0.0.1 / 8787 | where the engine listens |
| `AUTO_UPDATE` | true | update on every start |
| `CONSOLE_TOKEN` | *(empty)* | needed when `HOST` isn't this computer; sent as `X-Haze-Token` |
| `LLM_API_KEY` | *(empty)* | empty: the copilot runs offline |
| `LLM_BASE_URL` | Gemini's OpenAI endpoint | any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gemini-3.5-flash-lite` | must support tool calls |
| `LLM_TIMEOUT` | 45 | seconds per AI call |
| `CONSOLE_DRY_RUN` | true | blind on start; `false` lets frames out after Go live |
| `DMX_TRANSPORT` | artnet | `artnet` or `sacn` |
| `DMX_HOST` | *(auto)* | Art-Net: your network's broadcast; sACN: `multicast`; or one node's IP |
| `DMX_PORT` / `DMX_HZ` / `DMX_NET` | 6454 / 40 / 0 | port, frames a second (10–120), Art-Net Net (0–127) |
| `DMX_BLACKOUT_ON_EXIT` | false | blackout when Haze closes |
| `SACN_PRIORITY` / `SACN_SOURCE_NAME` | 100 / APP_NAME | sACN source details |
| `DMX_SYNC` | true | Art-Net: universes change together (ArtSync) |
| `SACN_SYNC_UNIVERSE` | 0 | sACN synchronisation universe (1–63999); 0 = off |
| `DMX_INPUT` | false | listen for Art-Net / sACN coming in |
| `MIDI_ENABLED` / `MIDI_DEVICE` / `MIDI_MAP` | true / first / data/midi_map.json | MIDI in |
| `CONSOLE_SHOW_DIR` | data/shows | show files |
| `CONSOLE_AUTOSAVE` / `CONSOLE_AUTORESTORE` | true / true | save the desk as you go and bring it back on start |
| `FIXTURE_DB` | data/fixtures.db | the fixture database |
| `FIXTURE_SEED_BUILTINS` | false | add generic profiles (their channels are guesses) |
| `CONSOLE_DATA_DIR` | `data/` | move the whole data folder (the tests use a scratch one) |
| `GDTF_SHARE_USER` / `GDTF_SHARE_PASSWORD` | *(empty)* | optional; you can also sign in from the app |

## When something's wrong

- **Lights don't respond:** check the top bar says **LIVE**, then
  Settings → Output: pick the node's address and read the network check (it
  says when this computer isn't on the node's network). **Diagnose** in the
  copilot checks the rest.
- **A playback does nothing:** Diagnose lists cues that point at lights no
  longer patched.
- **The copilot says offline:** add an AI in Settings → AI. The offline
  rules still handle common requests.
- **The offline AI's first answer is slow:** it's loading (up to a
  minute); after 10 idle minutes it unloads to free the graphics card.
- **"Unauthorised" or "unexpected Host header":** the desk is protected
  with a code, or was opened by a network name. Use the pairing code from
  Desk → Other computers, or open it as `localhost`.
- **GDTF Share says "not signed in":** press **Sign in** in Add lights, or
  put `GDTF_SHARE_USER` and `GDTF_SHARE_PASSWORD` in `.env`.
- **Haze froze:** it writes a report to `freeze_reports` in its data folder.
  Send that with a bug report.

## Reporting a bug

The quickest way: right-click the light → **Report a problem with this
light…**, or **Help → Report a bug**. Haze packs the screenshot, the light's
DMX and the show for you.

Or open **Issues → New issue** and pick **Problem with a light**,
**Something else is wrong** or **Request a fixture**. Never attach `.env`.

When something goes wrong inside the desk, a **⚠** badge appears in the top
bar: click it to see what happened and report it in one step.

## Not built yet

- Checking against more real nodes and fixtures (the light check and
  [docs/REAL_LIGHT_CHECKLIST.md](docs/REAL_LIGHT_CHECKLIST.md) cover the ones
  you have).
- Lasers and effects whose fixture file doesn't say which channel fires
  them: Haze won't fire what it can't identify, so add the light from its
  manual instead.
- Lights whose channels change meaning with a mode channel: Haze drives such
  a channel as one thing.
- Everything else planned: [docs/BACKLOG.md](docs/BACKLOG.md).
