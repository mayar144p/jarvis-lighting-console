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
| **Stage** (centre) | the 3D venue; views and saved views, frame selection, house lights, haze, crowd, zones, **Arrange** mode, full screen, and a *now playing* strip |
| **Programmer** (right) | tabs for what the selection can actually do: Level (and shutter open/close for lights without a dimmer), Colour (HSV picker, swatches, hex, white temperature, wheel steps), Position (pan/tilt pad, aim in degrees, **aim at a spot** or a performer mark, home), Beam (strobe speeds, gobo/prism steps, zoom/iris/frost, every attribute), FX, Looks (palettes and presets), Tools (fan, arrange, limits) |
| **Bottom panel** | three modes: **Faders** (cue stacks with GO / back / release, crossfade time, a per-cue fade / hold / follow timeline), **Buttons** (quick buttons) and **Timeline** (the show against the music), plus the grand master and BLACKOUT |
| **Status bar** | output target and rate, network, feed health, last save |

It is made for a desktop or laptop screen (1280 px wide and up).

### The venue

The stage sits in a real room: floor, walls, ceiling or an open roof, a stage
deck, zones (dance floor, bar, seating, VIP, DJ, FOH) and the rigging lights
go on. A fresh desk opens in a **club**. **Settings → Venue** swaps in a
template (club, small club, warehouse, concert stage, theatre, ballroom,
outdoor stage) and resizes the room.

* **House lights** (bottom right of the stage) make the room visible with the
  rig dark. Walls between you and the stage are cut away automatically.
* **Rigging**: truss, pipes, towers, ladders, tripod stands and floor bases.
  New lights place themselves by type: movers and washes on the trusses,
  PARs and bars on the stage lip. A light on a truss is *mounted*, so moving
  the truss moves its lights.
* **Crowd ▾**: show or hide the crowd, simple or varied figures, how packed
  it is, dancing or still. People stand in the zones and are lit by the rig.
* **Views ▾**: front, sides, back, plan and whole room. There's also eye level
  from the crowd, the DJ's view, and **through the selected light** along its
  beam, the way you would check a focus. Save any view by name.

### Arrange mode

**Arrange** (stage toolbar) turns the stage into an editor:

* Click a light, truss, object, zone, the stage or the floor plan to get a
  **3D gizmo**. Drag its arrows to move in X, Y and Z (5 cm snap). **E**
  rotates, which is also how a fixed light is aimed. Truss ends and zone
  corners have their own yellow handles.
* Drop a light near a truss and it **snaps on** and hangs from it. Drag a
  whole selection and drop it on a truss to hang the whole row.
* An inspector gives exact numbers: position, which rig and where along it,
  hang or stand, aim; truss ends and height; object size and rotation.
* **+ Add** puts in rigging (truss, pipe, tower, ladder, stand, floor base)
  and objects (DJ booth, bar, speakers, subs, pillars, LED screen, risers,
  tables, balcony, walls, performer marks).
* **Draw room** traces the walls by clicking corners, any shape.
  **Draw zone** draws a dance floor, bar, seating area and so on.
* **Floor plan** uploads an image or a **PDF** of the venue's plan. Set its
  scale by clicking two points and typing the real distance, then trace over
  it.

Outside Arrange mode a click only selects, so nothing moves by accident
during a show.

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
* **Smooth on ordinary laptops.** Fixture parts and truss are merged into a few
  draw calls, and the crowd is lit per vertex. The view only redraws when
  something changes. **Settings → 3D view → Quality: Auto** lowers the
  resolution when frames run long and wins it back when there is headroom.

Views: <kbd>1</kbd>–<kbd>6</kbd> (front, house left, house right, back, plan,
whole room), <kbd>F</kbd> to frame the selection.

### Quick buttons

**Buttons** in the bottom panel is a grid of instant buttons, 4 pages of 24,
like a MagicQ execute window. Each one is **hold** (on while pressed),
**latch** (press on, press off) or **tap**:

| kind | does |
|---|---|
| Flash | the target to full, optionally in a colour |
| Strobe | the target strobes, 1–20 flashes a second |
| Colour bump | overrides the target's colour |
| Kill / Blackout | the target, or everything, off while held |
| Effect | runs an effect on the target |
| GO / Release | a playback (GO can jump to a cue) |
| Preset | applies a recorded preset |

Targets are all lights, a group, a type (movers, PARs, washes, bars, beams) or
a fixed set of lights. **Suggest buttons for this rig** fills a page from what
is patched. Blackout and the grand master still win over a flash. Pressing a
button is never an undo step; setting one up is. Buttons save with the show.

### Timeline

**Timeline** in the bottom panel runs the show against the music:

* **Tracks.** *Cue* tracks GO a playback to a cue at a time. *Button* tracks
  hold a quick button for a clip's length, such as strobe hits on the beat.
  *Effect* tracks run an effect on a group or type. *Level* tracks automate a
  playback fader or the grand master with keyframes.
* **Audio.** Add an MP3, WAV, OGG, FLAC or M4A; its waveform is drawn under the
  tracks. Set BPM by typing, tapping, or **detect** it from the audio. There
  is a beat and bar grid, snap (beat, bar, seconds, off; Alt for free),
  markers, zoom (Ctrl+wheel) and loop.
* **Editing.** Double-click a lane to add a clip. Drag a clip to move it and
  its edge to resize it; right-click to edit, duplicate or delete. **+ Track →
  Cue list from PB1** lays an existing cue list out in time.
* The **engine owns the clock**, so the lights stay in time even if the browser
  stutters, and the audio follows it. Seeking puts the rig where it would be
  at that moment. <kbd>Shift</kbd>+<kbd>Space</kbd> plays and pauses.

### Follow me speed

Move tab -> Aim: **Follow speed** sets how the beams follow the pointer on
the floor map or the 3D floor. **Instant** goes straight there, as fast as
the lights can move. **Fast**, **Medium** and **Slow** make the beams glide
after it and carry on to where you let go (a dashed ring shows where they
are aimed right now). The desk does the gliding at the full DMX rate, so
the real lights glide smoothly even with the page hidden or closed. Nudging
or re-aiming the lights by hand stops a glide. Remembered on this computer.

**Make a button** (Move tab, and in the Roam row while roaming) makes a
button straight away for what the selected lights are doing: the roam
(its zones, speed and size), a shape or a movement with its knobs. It is
for those lights only, or for their group when the selection is exactly a
group (lights added to the group later follow it). Rename it in Edit.

**Stopping effects:** the "In the programmer" bar shows **Effects** with
a ×: it stops the effects you started and leaves the lights' colour, level
and position (and the effects of cues, buttons and the timeline) alone.
The FX tab's Running list says where each effect comes from; **Stop mine**
and **Stop all** are there too. **Clear all** no longer stops the effects
of cues and buttons.

### Only what a light can do

The pan / tilt pad shows the selected lights' real travel: their degrees
on the edges, the readout in degrees, and the part they can't reach (their
limits, the dance floor when it's locked) greyed out. A drag stops at the
limit. A light that only tilts (a CO2 jet, some bars) gets a tilt-only pad,
only the movements it can do (Bounce), and no aim tools. The Level, Colour,
Beam and FX tabs are for lights. A laser's or effect machine's own colour
and settings are on its Laser or SFX tab.

### Painting the rig and shapes

* **FX tab -> Paint the rig.** A **gradient** of 2 to 6 colours across
  the room (left to right, from the stage out, bottom to top, centre out,
  round the room), still, scrolling or on the beat. Or a **picture or
  video** laid over the lights as seen from the front or from above. A
  picture is kept with the show. A video plays in the browser that opened
  it, and the lights follow while it plays. Pixel bars and panels take it
  cell by cell. It paints colour, not brightness: bring the lights up
  first.
* **Move tab -> Shapes.** Draw your own movement: key points round where
  the lights aim, a smooth curve or straight lines, starting from a
  triangle, square, star, wave or zig-zag if you like. It runs with the
  tab's size, speed, direction and wave.
* Cues record what is running, including gradients, pictures, shapes and
  step effects, and GO plays them again. An effect button can run one of
  your step effects, on the beat if you like.

### On the beat

The tempo pill in the top bar is the desk's beat clock: tap it, type a BPM,
or follow MIDI clock, the CDJs (Pro DJ Link), the room's sound, or
**Ableton Link** (tempo menu -> *Follow Ableton Link*: Live, Traktor,
rekordbox, djay and the rest on the same network; the desk listens to the
session's tempo and its place in the bar, and joins no session of its own).

* **Buttons on the beat** (Buttons page -> *On the beat*: off, half beat,
  beat, 2 beats or bar). A press waits for the next one, and the tile blinks
  while it waits. Pressed just after the beat, it fires at once. A quick tap
  between beats still gives a hit on the beat. Each button can say
  otherwise in Edit (*Fires*: like the page, as pressed, on the beat, on the
  bar). Kill, ARM, E-stop, tempo, faders and XY pads always act at once.
* **The sound** (tempo menu -> *Sound*): pick the input (a line in from the
  mixer is steadier than a microphone). A link can move the effects' **size**
  as well as their speed and the brightness: loud is the effect as made,
  quiet shrinks the movements and the dimmer effects' depth.

### Keyboard

| key | does | key | does |
|---|---|---|---|
| <kbd>Space</kbd> / <kbd>Enter</kbd> | GO on the focused playback | <kbd>B</kbd> | cue back |
| <kbd>X</kbd> | toggle BLACKOUT | <kbd>C</kbd> | clear the programmer |
| <kbd>A</kbd> / <kbd>Shift</kbd>+<kbd>A</kbd> | select all / none | <kbd>L</kbd> | locate |
| <kbd>1</kbd>–<kbd>9</kbd> | select head (<kbd>Shift</kbd> adds) | <kbd>↑</kbd> <kbd>↓</kbd> | intensity ±5 (<kbd>Shift</kbd> ±1) |
| <kbd>R</kbd> | record a cue | <kbd>G</kbd> | group the selection |
| <kbd>O</kbd> | overwrite the current cue | <kbd>I</kbd> / <kbd>D</kbd> | insert after / delete the current cue |
| <kbd>F</kbd> | frame the selection | <kbd>/</kbd> or <kbd>Ctrl</kbd>+<kbd>K</kbd> | command bar |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd> | undo / redo | <kbd>Ctrl</kbd>+<kbd>S</kbd> | save show |
| <kbd>?</kbd> | help | <kbd>Esc</kbd> | close the top panel |
| <kbd>Shift</kbd>+<kbd>Space</kbd> | play / pause the timeline | <kbd>W</kbd> / <kbd>E</kbd> | Arrange: move / rotate |

Typing in a field never fires the rig.

### Command bar

<kbd>/</kbd> opens it. It searches every action and also takes desk syntax:
`1-4 red`, `1-8 at 60`, `all dimmer 40`, `1-4 pan 90`, `cue 3 go`.

## The copilot

### The AI assistant (with an AI key)

With `LLM_API_KEY` in `.env`, the copilot's Program tab is an assistant
that works the desk itself. It looks at the rig, does things, then
**checks the real lights** (level, colour, which zone each beam lands in)
and fixes what isn't right, in as many rounds as it needs. Ask for a feeling
(*"the drop needs to hit harder"*, *"calmer for the speeches"*, *"this looks
cheap, fix it"*) or ask a question (*"why is head 7 dark?"*). It asks you
when something is unclear, with answers to tap. It remembers your
preferences (**What I remember**, with a × to forget one). **AI sees the 3D
view** sends a small picture of the view with each message, and **Photo…**
(or paste / drop) gives it a picture to match.

Everything it does runs in 3D first (**Keep** / **Throw away**) and is one
Ctrl+Z. It can't arm the output, fire effects or lasers, save or load
shows, or delete anything; it asks you instead. **New chat** starts over.
Tick *Offline compiler* (or have no key) for the keyword copilot described
below.

**AI** in the top bar opens it. Three tabs:

* **Program.** *“Warm wash on the pars at 70%, then a slow rainbow on the
  movers.”* The request, your rig (types, groups, palettes, cues, selection)
  and recent turns go to the model, which returns a structured plan through a
  forced tool call. You see the steps first, then apply them. They run as
  **one** undo step labelled *copilot*, and if any step fails the whole plan
  rolls back. Without a key (or with *offline* ticked) a keyword compiler
  handles common requests: levels, colours, named effects (rainbow, circle,
  figure eight, pan sweep, breathe, dimmer chase, sparks), aim, beam, fades,
  and targets like *movers* or *pars*. The copilot also knows the room: it can
  aim at a spot or mark, hang lights on a named truss, pick a venue template
  and play the timeline. It also programs:
  * *"The back truss chases red and white on the beat during the drop"*: a
    step effect of the show on the lights of that truss (back = rear =
    upstage), placed as a clip over the drop on the timeline (a marker, or
    a section of an AI show).
  * *"Build me 8 buttons"*: the most useful buttons for the rig on the
    first empty page, or a focus (*"6 strobe buttons"*).
  * *"A 32-bar build-up"*: a chase that doubles its speed every quarter,
    pulsing in sixteenths in the last bar, with the master climbing. It ends
    where the drop starts.

  **Preview in 3D** runs a plan in blind: the 3D view shows it, effects
  included, and the real lights keep what they have. **Keep** hands it to
  the rig, **Throw away** drops it. Cues, blackout, the master and the
  timeline still go live.
* **Design a show → Build the whole show.** The copilot reads the rig in the
  3D view and the song on the timeline, then designs and builds the whole
  show:
  * **What it reads:** what each light is, where it hangs (front truss movers,
    stage-lip PARs, tower strobes…), what it can do, and the room's aim
    targets (DJ, dance floor, crowd, back wall).
  * **The design:** song sections (intro, build, drop, breakdown…), with a look
    per group, effects the group can actually run, strobe and flash hits on
    the beat, and master fades.
  * **What it builds:** a cue per section, a track per group of effects, the
    hits on the beat grid and the master automation, all laid out on bars
    and stretched to fit the song.

  You see the plan first. Building it is one <kbd>Ctrl</kbd>+<kbd>Z</kbd>, and
  your own timeline tracks are kept. It works offline too, with a style-aware
  designer; pick a style chip (techno, house, EDM, hip hop, lounge, wedding,
  latin) or describe it.
* **Design a show → concepts.** A brief (*winter wedding, slow and elegant,
  warm whites, avoid red*) returns two or three concepts, each with a
  palette and cue list, for one playback.
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

Every venue's node has its own address, so you set it in the app for each
venue. Open **Settings → Output**:

1. **Send DMX to:**
   * **Auto** broadcasts on the network facing the rig. The Art-Net 2.x
     range comes first, using the adapter's real mask (2.0.255.255 on a
     2.0.x.x/255.255.0.0 network).
   * **One node** sends to that IP only. This is the most reliable option,
     for example a Chauvet DMXAN2 labelled `2.0.0.10`.
   * **Broadcast** sends to an address you give it.
2. **Find nodes** polls every network the computer is on. Click **Use this
   node** next to the one you want. Some nodes don't answer polls; if so,
   type the IP from its label.
3. The network check tells you when the computer has no address on the
   node's network, and what to set. For a node at `2.0.0.10 / 255.255.0.0`,
   give the Ethernet port a fixed address such as `2.0.0.100`, mask
   `255.255.0.0`, and no gateway, and keep Wi-Fi on for the internet.
4. The target is saved with the show, so each venue's show file brings its
   own node back. It changes live, with no restart.

   `DMX_HOST` and `DMX_TRANSPORT` in `.env` still set the default that
   **Auto** uses. Universe 1 in Jarvis is Art-Net universe 0, which is
   port A on most nodes. The OS firewall must allow Python on private
   networks.
5. Press **GO LIVE**.

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

**Add fixtures** searches everything at once in the **All** tab, and
also has separate tabs:

* **Installed:** everything already in your library.
* **Libraries:** the [Open Fixture Library](https://open-fixture-library.org)
  (MIT) and the [QLC+](https://www.qlcplus.org) fixture definitions
  (Apache 2.0). Together that's about 2,400 lights, including budget DJ
  fixtures that aren't on GDTF Share (for example the Chauvet Intimidator
  Wave 360). Both ship with Jarvis, so they're searchable offline at a
  venue. **Install and add** puts the fixture in your library and patches
  it.
* **GDTF Share:** [GDTF Share](https://gdtf-share.com), the manufacturers'
  own files (free account). You can search and download in the app.

You can also drop `.gdtf`, QLC+ `.qxf` or Open Fixture Library `.json` files
into `fixtures_inbox/` and press **Import** in Settings, or run
`python tools/import_gdtf.py`.

Every format is read into the same model: DMX modes, 16-bit channels, pan and
tilt travel, the shutter's open value (so a light whose shutter reads 0 as
closed still lights on **Full**), and the colour and gobo wheel slots with
their names and colours. Community libraries are sometimes wrong, so check
an unfamiliar light's mode against its manual. Downloaded files stay on your
machine and are never committed (`data/` and `*.gdtf` are git-ignored).

**Test this light.** A fixture file can be wrong in ways Jarvis can't see: a
shutter whose open value it never states, or a channel order that doesn't match
the light's mode. If a new light misbehaves, run the 30-second test: right-click
it → **Test this light…** (also on the Level tab's warning). Go live first, then:

1. Jarvis lights it at full, white and centred, and asks whether the real light
   is on.
2. If it's dark, Jarvis tries the shutter's likely open values one at a time. Press
   **It's on!** when the light comes on. If none work, it tries each of the light's
   other channels the same way.
   If it's still dark, or you'd rather skip ahead, **Set channels by hand** gives
   every DMX channel of the light its own fader, sent straight to the light. Move
   them until the real light comes on, then press **It's on - keep these**. That
   works even when the fixture file is wrong. A channel the file never named is
   then held at that value for the model.
3. It then moves pan and tilt, and shows red, green and blue, asking each time
   whether the real light did the same. A wrong answer usually means the light's
   DMX mode doesn't match the one you added, and Jarvis tells you which mode to set.

What works is saved for that model, so every head of it lights on **Full** from
then on, even after a re-download. A model that passed isn't asked about again.

**Match the 3D view to the real lights.** The visualiser moves heads with a
motor model: it speeds up, travels and brakes, and follows the Pan/Tilt Speed
channel, instead of snapping into place. To match your light exactly,
right-click it → **Calibrate movement speed…**. The real head sweeps pan (then
tilt) at top speed and you tap when it stops. The time is saved for that
fixture model. Strobes show on screen only when the light really strobes: a
strobe button, or the strobe channel inside a range its fixture file marks as
strobe, never an "open" value.

To refresh the bundled libraries from upstream, run
`python tools/build_fixture_libraries.py`. `app/fixlib/NOTICE.md` has the
licences and the exact upstream commits.

## Lasers and special effects

Confetti, CO₂, flame, sparks, fog, haze and lasers are **effects**, not
lights, and Jarvis treats them that way.

**How effects are recognised.** Every fixture Jarvis stores is sorted into
light, laser or SFX, whatever its source. An effect's channels get their
own jobs: *fire*, *arm*, *fog output*, *laser output*, *pattern* and so on.
The "fire", "armed" and "on" values come from the fixture's own chart and
are never guessed. For example, a MagicFX Psyco2Jet's safety channel is
armed at 100–155, and 156–255 is its **test mode**.

**Beam bars** (like the Laserworld BeamBar 10B MK3, in the Jarvis library) get
one control per beam. On the **Laser** tab:

- Tap beams on and off, or lay a pattern across the bar: All, Odd, Even, Left,
  Right, Centre or Ends. **Beam level** sets how bright the lit beams are.
- Beams record into cues like any attribute. Record a few patterns on one
  playback to make a beam chase.
- **Output mode** picks what the laser does when fired: your own beams, its
  built-in programs, auto or sound. It records into cues like the beams, so
  each cue can switch mode. A laser button can also carry its own mode.
- Any channel the tab doesn't know by name appears under **Other channels**, so
  every channel has a control.

The beams still only light while the laser is **armed and fired**. With no
beams programmed, firing lights them all.

**What never reaches an effect:**
- light controls: Flash, Strobe, Full, colour, Locate, effects, the
  master and **Select all** (lights only);
- cues, the programmer and the auto show;
- the AI copilot, which is denied every effects action.

**Blackout** stops every effect.

**How effects run:**
- **ARM FX** (top bar) arms fire and laser output. It turns itself off
  after 10 minutes and shows a countdown. **KILL FX** stops everything
  and disarms. Blackout and loading a show also disarm, and the desk
  always starts disarmed.
- **FX buttons:** an automatic **FX** button page has ARM, KILL, confetti
  shot/hold, CO₂/flame bursts, fog (10 s, hold, haze) and laser
  hold/latch. You can make your own in the button editor.
- **Time limits:** every burst stops at the machine's limit, even if a
  button sticks: a few seconds for CO₂ and flame, and the tank time for
  confetti. The Funfetti Shot empties in 25 s, and its tank counts down
  until you press **Reload**.
- **Programmer tabs:** **Laser** has hold-to-fire output, pattern and
  colour chips, size, rotation, position and speed. **SFX** has
  hold-to-fire, 1 s shots, tank levels, and fog level and timer.
- **Recording:** patterns and other settings are recorded in cues like any
  attribute. Output never is.
- **3D view:** laser fans, confetti bursts, CO₂ plumes, flames, sparks and
  fog, exactly while the real machine is firing.

**Adding a fixture that isn't in any library.** In **Add fixtures**, click
**From its manual…**, then paste the DMX chart or drop the manual's PDF.
With an AI key the copilot reads it; without one the offline reader
handles the usual chart layouts, including multi-language manuals. You
check the table, then save it to your library.

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

## Tests

```
python tools/selftest.py
```

About 1,700 checks: GDTF parsing and geometry, the fixture database, the
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

CI (`.github/workflows/ci.yml`) runs it on Linux and Windows, plus a lint pass.

The other checks (all in `tools/`, all on scratch data - `data/` is never
touched):

| Check | What it proves | Time |
|---|---|---|
| `gigcheck.py` | a whole gig, then save, reload, compare (CI) | <1 s |
| `oddcheck.py` | odd inputs: empty rig, 12,900 nonsense values, 520 lights, corrupt shows and autosaves, a restart, Undo all the way (CI) | ~1 min |
| `libsweep.py [--all-modes]` | every library light (and every mode: 8,093) x Full, Blackout, colour, gobo, move, Locate, effects, SFX - DMX vs 3D | ~3 min |
| `uicheck.mjs [--big]` | every screen and dialog at 1280 / 1440 / 1920: spills, tiny or off-screen controls, page errors; `--big` with 124 lights | ~20 min |
| `vischeck.mjs` | 124 real lights programmed through the screen: DMX, the 3D's targets and the drawn models agree for every light | ~5 min |
| `brands.py [--brand X \| --product "words"] --check` | the top 20 brands, 15 products each (effects machines first): libsweep's checks plus "fire / lasers only when armed", even with their channels programmed | ~4 min |
| `vischeck.mjs --brands \| --brand X \| --product "words"` | the same rigs through the screen, one brand at a time, plus the effects: disarmed nothing fires; armed, fire / fog / laser show in the 3D; KILL stops them | ~3 min a brand |
| `oddcheck_ui.mjs` | two browsers on one desk; the server dropping out and coming back | ~1 min |
| `frametiming.py` | DMX on a steady 40 Hz with a big rig and three screens (CI) | 5 s |

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
│   ├── engine_*.py        the Engine's parts (mixins): patch, rig, quick, fxlayer,
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

## Troubleshooting

* **Copilot says offline:** no `LLM_API_KEY`. The offline compiler still
  handles common requests.
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

* A per-fixture colour-wheel map (the AI and the colour buttons only colour
  lights that mix colour; wheel slots are offered as numbered steps).

* Validation against physical nodes and fixtures.
* Show file management (rename, duplicate, delete) beyond save and load.
* A packaged desktop build.

## Reporting a bug

Open the repo's **Issues** tab → **New issue** and pick a form:

- **Problem with a light** - a fixture does the wrong thing on the rig, in
  3D or in the programmer (say which light, which mode, what happened).
- **Something else is wrong** - screens, playbacks, output, saving...

Drag in a screenshot, the light's DMX (its row → ⋯ → Show DMX channels)
and, if you can, the show file so the exact rig can be loaded.  Never
attach `.env`.  A "Report a problem with this light…" button in the desk
itself is planned (docs/BACKLOG.md A11).
