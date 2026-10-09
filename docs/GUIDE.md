# Jarvis — user guide

How to use the desk. Getting it running is in the [README](../README.md)'s quick start.

## The desk

| Region | What it does |
|---|---|
| **Top bar** | show name, output state and **GO LIVE**, undo/redo with the last action named, command palette (<kbd>Ctrl</kbd>+<kbd>K</kbd>), copilot, patch lock, settings, help |
| **Fixtures** (left) | the patch as a table; filter, group chips, add heads, select by click / ctrl-click / shift-click, select similar. **Your groups are folders**, like a code editor's file tree: ▸ opens one, clicking its name selects all its lights, hovering it rings them in the 3D view (double-click frames them). Lights in no group stay in the list below |
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
* **Lights only**: hides the room, truss, stage, objects and crowd, so only
  the lights and their beams are left (**Arrange** turns it off).
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
  truss and the performers, with bloom and optional haze. A lens that looks
  straight at the camera draws a thin flare streak, as a camera sees it.
* **Gobos.** A light's own gobo pictures are projected for the slot its gobo
  channel is in. They come from its fixture file; when that file has none,
  from the same light in the other library (the two often describe one light,
  only one with pictures), or from the maker's own GDTF. A light still drawn
  with stand-in patterns says so on the Beam tab: **Get the maker's gobos**
  fetches its GDTF from GDTF Share (signed in; the same as Stage → **The
  makers' 3D bodies and gobos**). The gobo you pick always sends the value
  the 3D shows - a library check proves it on every light.
* **Effect bars** (PARs, derbys, strobes and a laser on one bar) are drawn
  as the bar they are; the desk still treats them as a laser, so the laser
  part needs ARM.
* The fixture picker shows the same 3D model before you patch anything.
* **Smooth on ordinary laptops.** Fixture parts and truss are merged into a few
  draw calls, and the crowd is lit per vertex. The view only redraws when
  something changes. **Settings → 3D view → Quality: Auto** lowers the
  resolution when frames run long and wins it back when there is headroom.

Views: <kbd>1</kbd>–<kbd>6</kbd> (front, house left, house right, back, plan,
whole room), <kbd>F</kbd> to frame the selection.

### Quick buttons

**Buttons** in the bottom panel is a grid of instant buttons on up to 8
pages, like a MagicQ execute window. Each one is **hold** (on while pressed),
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

**How they look** (Edit -> a button -> *Look*, with a live preview of the
tile off and on): its colour; its size, 1-4 buttons across and 1-3 down;
its shape (rounded, square, pill, circle); its fill (outline, solid, glow);
text size S-XL; *Name only* (no small line under it); *Blink while on*
(slow, and still when the computer asks for less motion); one of 32 icons,
or the icon alone. A lit button shows a dot as well as its colour.
**Layout** (in Edit) sets the page's grid: 4-12 buttons across (fewer =
bigger), the rows, and the row height, for one page or every page.
Right-click a button in Edit to **Copy look** and **Paste look** onto
another button or the whole page; what the buttons do stays the same.

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
* **Timecode.** The **TC** button follows timecode: *MIDI timecode (MTC)* from
  the desk's MIDI input, or *audio timecode (LTC)* - a timecode track into the
  sound input picked in Sound (a line in from the playback computer). The
  timeline jumps to it and plays along, and pauses when it stops; you say
  which timecode is the timeline's 0.
* The **engine owns the clock**, so the lights stay in time even if the browser
  stutters, and the audio follows it. Seeking puts the rig where it would be
  at that moment. <kbd>Shift</kbd>+<kbd>Space</kbd> plays and pauses.

### Turned trusses and the room map

Turn a truss in Arrange and the lights on it turn with it, just as the real
fixtures do when the truss is hung at an angle. Aiming still lands on the
spot you point at (the room map, Follow me, positions, the assistant): the
desk works out each light's pan and tilt from the way its base now faces,
so the 3D view and the real lights agree. A light keeps its own pan and
tilt when you slide it along the same truss.

The room map in Move tab -> Aim draws the room as it really is: its own
outline (L-shaped, round...), the stage, zones, the bar and other objects,
the trusses at their real angle, and each light turned with its truss
(selected lights in yellow). Drag on it and every selected light points at
that spot. The pan/tilt dials still move each light's own pan and tilt.

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

* **The CDJs** (tempo menu -> *Listen to the CDJs*): with two decks playing,
  the desk follows the one the players say is **master**, and the tempo's
  hover text names it (*from CDJ-B (master)*). With no master it stays on the
  deck it is following until that one goes quiet for 2 seconds, so it never
  jumps between two tempos mid-mix.
* **The phrase** (tempo menu -> *Phrase starts here* on the drop or the
  first beat of a section): the desk counts 8 bars from it (*bar 3 of 8*).
* **Buttons on the beat** (Buttons page -> *On the beat*: off, half beat,
  beat, 2 beats, bar, 2 bars, 4 bars or the phrase). A press waits for the next one, and the tile blinks
  while it waits. Pressed just after the beat, it fires at once. A quick tap
  between beats still gives a hit on the beat. Each button can say
  otherwise in Edit (*Fires*: like the page, as pressed, on the beat, on the
  bar, on the phrase). Kill, ARM, E-stop, tempo, faders and XY pads always act at once.
* **The sound** (tempo menu -> *Sound*): pick the input (a line in from the
  mixer is steadier than a microphone). A link can move the effects' **size**
  as well as their speed and the brightness: loud is the effect as made,
  quiet shrinks the movements and the dimmer effects' depth.

### Shows

The show name in the top bar opens the **show menu**: New show (a template
or empty), Save (Ctrl+S), Save as, and the first 8 shows (A to Z) to open.
**All shows…** lists every one with when it was saved and its earlier
versions; find one by name, and **Open**, **Rename**, **Copy** (start next
week's venue from this one) or **Delete** it. A deleted show goes to the
shows folder's `.bin` (the last 20 are kept), not gone. Renaming, copying
and deleting need Design mode, like saving. The desk also autosaves as you
work, and **Earlier versions…** opens any saved state of the show.

### A video for the client

View -> **Record a video…** films the 3D view: pick the camera first, then
either *play the timeline from the start* (it records the whole timeline and
stops at its end) or *record what I do* while you run the show by hand (up to
10 minutes; the red REC button on the 3D view stops early). Full HD, 4K or a
small one for chat apps; saved as MP4 (WebM where the browser can't make
MP4), named after the show. Only the 3D picture is filmed, not the desk
around it, and the real lights needn't be connected - program at home, send
the client the video. Playing the timeline, its music goes in the video too.
A slow computer films smoother at Small.

### Workspaces

The top bar's **workspace** button arranges the screen for the job:
*Programming*, *Busking* (buttons big, no fixture list), *Theatre (cues)*
(a taller cue dock) and *Show (run only)* (the 3D view and the buttons).
Alt+1 ... Alt+9 switches between them. Each one keeps the screen as you
left it: the fixture list and programmer shown or hidden, which side each
is on (drag a panel by its title to the side you want - both can go on the
same side - or pick it in the workspace menu), or floating over the 3D view
(drag it by its title anywhere over the 3D; drag it near a side to dock it
again), their widths (drag a panel's
inner edge; double-click for the normal width), the faders / buttons dock at
the bottom or the top and its height, the programmer's tab. *Save as a new workspace* makes your own. Workspaces belong to this
computer (each operator's own), not to the show.

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

It works the whole desk in words: it adds lights from the library (*"add a
Chauvet Intimidator Spot 260"*; it asks when several fit and says so when a
model isn't there), places them (*"middle of the front truss"*, *"both
ends"*, *"hang the rear truss at 4 m"*), changes the room (trusses, poles,
zones, marks, its size), and makes, edits, renames and deletes groups, cues,
playbacks, buttons, the timeline and palettes. It knows what each light can
really do, from its own file, and says what it couldn't: *"the 6 PARs can't
tilt, so they stay"*. It talks about the desk and nothing else.

Everything it does runs in 3D first (**Keep** / **Throw away**) and is one
Ctrl+Z. Going live, arming, firing confetti / CO2 / lasers / haze, saving or
opening a show and every delete are only **prepared**: each shows up with
**Do it** / **No**, and nothing happens until you tap. Calibration, the
network, locks and undo stay yours. **New chat** starts over.

`python tools/aicheck.py` asks your AI (Settings -> AI; `--mode local` or
`--mode online`) about 50 real sentences on a scratch rig and checks the
desk after each one.
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

## New shows: templates and the demo

The first time the desk opens with nothing patched, it offers **the demo**:
a club night already programmed and playing in the 3D view (nothing leaves
the computer). It also offers a **template**, or an empty show, and a short
**tour** of the desk (later: Help → *Take the tour*).

**Show ▾ → New show…** has the templates: **Club night**, **Wedding**,
**Band / concert**, **Theatre** and **Corporate event**. Each one builds:
- a room of that kind;
- real lights from the library, hung on its trusses;
- groups;
- colour palettes, and position palettes for each zone of the room;
- a page of buttons;
- a first set of cues and the timeline.

It's a normal show, so change anything. A template, or **Empty show**, is
one Ctrl+Z, and that brings back the show that was open.

## Look and help

**Settings → Screen & 3D**, per computer:
- **Show dark:** the panels in dim red for a dark venue. Eyes stay used to
  the dark, and the 3D view keeps its true colours.
- **Panel brightness** and an **accent colour**.
- **Hover help:** rest the pointer on any control for what it does (on by
  default).

**Wheels:** under the programmer's tabs, a wheel for each attribute of the
open tab that the selected lights have (dimmer, colour, pan / tilt, zoom,
focus, iris, frost, gobo spin). Drag up or down or scroll to turn. **FINE**,
or holding Shift, turns a tenth as far. Arrow keys work too.

**More universes:** Settings → Output → *More universes* sends any universe
to a node of its own (one node per truss), over Art-Net or sACN. With sACN
you can also set the desk's **priority** (0-200). Both are saved with the
show.

## OSC out (QLab, Resolume, video servers)

**Settings → MIDI & OSC → OSC out**: the address and port of the other
program (QLab listens on 53000, Resolume on 7000). **Test** sends
`/jarvis/hello`. Tick *Tell it what the desk does* and it gets
`/jarvis/go <list> <cue>`, `/jarvis/master 0-1`, `/jarvis/blackout 0|1` and
`/jarvis/button <id>` as they happen. A cue sends its own: Cue list → ⋯ →
**Actions…** → **Send OSC** (e.g. `/go`, or
`/composition/layers/1/clips/2/connect 1`). Saved with the show, since each
venue has its own video computer.

## Hardware controllers

Switch on **Settings → MIDI & OSC → MIDI in the browser**, then plug in a
controller. These are recognised by name and work straight away:

| Controller | Pads | Faders | Buttons |
|---|---|---|---|
| Akai APC mini / APC mini mk2 | the buttons page, lit in each button's colour (dim off, full or blinking on) | playbacks 1-8 and the grand master | under the pads: GO 1-8; down the side: buttons page 1-8 |
| Novation Launchpad Mini MK3 / X | the buttons page in each button's own colour | - | top row: GO 1-8; right side: buttons page 1-8 |
| Akai APC40 / APC40 mkII | the 5 x 8 clip grid: the buttons page in each button's colour (blinking / pulsing on) | playbacks 1-8 and the master | TRACK SELECT: GO 1-8; CLIP STOP: release; SCENE LAUNCH: buttons page 1-5 |
| Behringer X-Touch / X-Touch Compact (Mackie Control mode) | REC 1-8: the first 8 buttons | playbacks 1-8 and the grand master, **motor faders follow** | SELECT: GO, MUTE: release, SOLO: buttons page |

The APC40 layouts are built from Akai's published protocol and haven't been
tried on a real unit yet: if a button does the wrong thing, **Settings → MIDI
& OSC → MIDI monitor** shows what it sends - report it.

The pads always play the page that's on screen, and the screen follows the
page you pick on the controller. Other controllers still play buttons by
their MIDI note (Edit → a button → MIDI).

## MVR plots (Vectorworks, Capture, grandMA3…)

**Show ▾ -> Import MVR plot…** reads an `.mvr` file. Every light is
installed from its own GDTF inside the file, with its 3D body when the file
has one. It is patched at its universe and address, in its mode, and placed
where the plot has it, hanging or standing. The plot's own **trusses** come in
at their place, angle and length (the length from the truss's GDTF model, or
from its name such as "Pipe 4m"), and the lights hung along one go on it,
turned with it. Lights hung in a row with no truss get one of their own.
**Scene objects** the desk knows by name come in too: PA and subs, LED walls
and screens, the bar, the DJ booth, risers, pillars, tables (other objects,
like chairs, are counted and left out). With a show already patched you choose **Add to this
show** or **Start from the plot** (the old lights and trusses go, and the room
is sized to the plot). What couldn't come in is listed: a GDTF missing from
the file, or a mode the file doesn't have (the light gets its first mode).
The whole import is one Ctrl+Z.

A light whose GDTF isn't in the MVR comes in from the desk's own fixtures or
the libraries when one has the same maker and model (the import says so;
check its mode).

**Show ▾ -> Export as MVR** saves the patch, the trusses (at their angle,
with their length in the name) and the objects as an `.mvr`, with the GDTF
files the desk has for those lights, for other programs.

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

**New lights arrive by themselves.** Once a week a job on GitHub fetches
the newest Open Fixture Library and QLC+ fixtures, checks every light
against the desk's rules and runs the self-test, then opens a pull request
listing what's new (only when something changed). Once it's merged, every
desk gets the new lights the next time it starts, and they're still
searchable offline. The **Libraries** tab says when each library was last
updated.

**Request a fixture.** A light none of the libraries has? In **Add
fixtures**, press **Request it…** (or **Can't find it? Request it…** under
an empty search). Fill in the brand, model, mode and a link to the manual.
It opens a filled-in request on the desk's GitHub page; press Submit there
(that needs a GitHub account) and drag in the manual's PDF. Can't wait?
**From its manual…** adds the light yourself straight away.

Every format is read into the same model: DMX modes, 16-bit channels, pan and
tilt travel, the shutter's open value (so a light whose shutter reads 0 as
closed still lights on **Full**), and the colour and gobo wheel slots with
their names and colours. Community libraries are sometimes wrong, so check
an unfamiliar light's mode against its manual. Downloaded files stay on your
machine and are never committed (`data/` and `*.gdtf` are git-ignored).

**Test this light.** A fixture file can be wrong in ways Jarvis can't see: a
shutter whose open value it never states, or a channel order that doesn't match
the light's mode. If a new light misbehaves, run the 30-second test: right-click
it → **Test this light…** (also on the Level tab's warning, and offered when you
add a model the desk hasn't seen pass the test). Go live first, then:

0. Jarvis asks whether the light's own display (or DIP switches) shows the mode
   and address the desk expects. Most "some heads don't move / wrong colours"
   is a light set to another mode. **It shows another mode** lists the model's
   modes: pick the one on the display and every light of that model switches to
   it, keeping its number, place, groups and cues. If one has to move to a new
   address because the mode is bigger, Jarvis says which, so you can set it on
   the light.
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
   A light whose colour channel is its only on / off (a derby's "Color
   macro", a small LED's "Programs") and whose file calls 0 only "No
   function" is asked once more: with that channel at 0, is the real light
   **Dark** or **Lit**? The answer is kept for the model: Full lights it,
   Out and Blackout darken it, and the 3D draws it that way. **Ready?** lists
   such lights until they've been asked.
3. It then moves pan and tilt, shows red, green and blue, and strobes fast then
   slow, asking each time whether the real light did the same (on a multi-head
   light: all of its heads). A wrong answer usually means the light's
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
- the AI copilot: it only prepares arming and firing, and you tap **Do it**.

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
