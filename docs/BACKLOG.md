# Backlog

Work agreed with the operator.  **Part A is the open plan**, in the
suggested order; Part B (items 1-17) is DONE and keeps the notes on how it
was built.  Each item notes the cause already found in the code, so
whoever picks it up starts from the diagnosis, not from scratch.

# Part A - the plan (open)

## A0. Full debugging pass (DONE, 2026-09-30)

Tools: `tools/rigcheck.py` (every light type + ~15 brands + CO2 / flame /
spark / confetti / hazer / laser, hung / stood / dragged / snapped on
trusses, a pole and a pipe in an L-shaped room; every action and button
kind; DMX vs 3D feed: 4,266 checks) and `tools/rigcheck_3d.mjs` (the same
lights in the real visualiser: model, beam, where it lands, SFX firing).
Fixed:
- **Lights looking at the roof after "aim here":** a mode with fine pan but
  no fine tilt got a 16-bit tilt (full tilt, also on the rig); an
  unreachable spot left the light at home (up) - now as close as it can and
  it says so; lights high on a tower stood upright (now hang above 2 m);
  a light dragged up past 2 m kept "standing"; the solver used one pivot
  height for every model (now each 3D model's); scanners aimed as if the
  beam left upwards.
- **RGB-only lights (3/4/6-ch PARs, bars, panels): "Full" did nothing.**
  A virtual dimmer now scales their colour (white with none set) for the
  fader, cues, flash / dim buttons and the master.
- "Shutter, strobe, reset" channels were maintenance (light never opened);
  default modes skipped 1-channel "sound active" modes but still picked
  SFX modes that can't fire; lamp-only lights show lit (Ready? warns).
- Rigging: bars can be turned (rotate tool, Turn 90/45), resized, stood
  up / laid flat, hung from the ceiling; a drag stays inside the room and
  snaps to the ceiling; rigs off-screen or level with the camera (low
  rooms) are framed when selected; a resized room scales its zones.
- Programmer: stray "null" text on the FX / colour tabs; SFX-only
  selections no longer get Level / FX tabs.

## A1. Engineering (bugs, steady DMX, tablets, code health)

1. **(DONE)** The 8 bugs from the review of PR #28: undo after recording
   a cue loses its effects; cue effects survive loading another show; one
   unpatched light drops a whole cue effect; RDM ignores DMX_NET and > 32
   universes; show_versions / show_export reload every screen; hold-chip
   slide-off swallows the next tap; Ready? never clears after one DMX error.
2. **(DONE) Steady DMX with several screens.**  Measured before: 286
   lights, 3 screens, frame gaps up to 52-58 ms instead of 25.  Now: the
   effect lookup is answered once per role set (frame build 11.3 -> 5.3
   ms); the 3D look is made once per tick on the output thread just after
   the frame goes out and shared by every screen (p99 gap 25.1 ms with 3
   screens); a screen gets only the snapshot parts an edit changed
   (`snapdiff`); ArtSync (unicast Art-Net) and E1.31 sync
   (`SACN_SYNC_UNIVERSE`); `tools/frametiming.py` in CI; a timing dot in
   the status bar ("smooth" / "a few late frames" / "stuttering").
   Left: DMX and the 3D look from one resolve - no longer on the DMX
   path, so only CPU; do it with the engine split (item 4).
3. **(DONE) Tablets.**  Screen wake lock (Settings -> Keep this screen
   awake, on by default; browsers only allow it on https or the desk
   computer itself, and Settings says so); installable full-screen app
   (manifest, icons, "Install as an app" in Settings, Add to Home Screen
   on iPad); a stream that goes quiet for 3 s is dropped and re-opened
   with a full snapshot, retries are at most 2 s apart, and coming back
   online or to the tab retries at once.
4. **(DONE) Code health.**  engine.py (10.7k lines) is the Engine core
   (1.3k) plus twelve mixins, one per area, and engine_base.py for the
   shared constants and helpers (re-exported, so `from app.engine import
   X` still works).  selftest.py (11.8k) is a 320-line runner plus
   tools/selftests/ (common.py and eight parts).  app.css: the 22
   selectors defined twice are merged; the computed style of every element
   at four widths, gig mode on and off, every programmer tab, is unchanged.
5. **(DONE)** A built-in virtual Art-Net / RDM node (Settings -> Output
   -> Virtual node: the output goes to it on loopback; it shows every
   universe byte by byte with whose channel each is, the frame rate and
   ArtSyncs, and answers ArtPoll and RDM for every patched light, which
   can be readdressed without touching the patch) and a MIDI monitor
   (Settings -> MIDI: every message from the desk computer's controller
   with what it did, and every message a controller on the tablet sends).

## A2. Programming features (from grandMA3, MagicQ, Avolites, Onyx, QLC+, Lightkey, SoundSwitch, rekordbox)

6. **(DONE) A live beat clock** (app/tempo.py): a tempo pill in the top
   bar with a light on each beat (pink on the 1); tap it or press T,
   Shift+T is the 1; a typed BPM and nudges; MIDI clock from the desk's
   MIDI input or the browser's (Start = the 1); Pro DJ Link from the
   CDJs (tempo with the pitch fader and the beat of the bar).  The Speed
   master follows it (120 BPM = 1x) until moved by hand; any running
   effect or movement can lock to it (one cycle per 1/4 beat .. 8 bars,
   in phase with the bar, kept in cues and buttons).
   Left: Ableton Link, audio in (with item 7), buttons and strobes that
   fire on the next beat.
7. **(DONE) Sound-reactive control** (tempo menu -> Sound): a screen
   listens (microphone / line in; https or the desk computer itself) and
   sends loudness, bass, mids and highs (each with its own automatic
   gain), beats, a tempo estimate and drops (the bass back hard after a
   breakdown) ~25 times a second.  Links: a sound (or the beat as a
   pulse) moves everything's, a group's or some lights' brightness, or
   the effects' speed, with depth and sensitivity; triggers: each beat /
   bar / drop (every Nth) presses a button; the room's beat can be the
   tempo.  With nothing listening nothing is dimmed.  Saved with the show.
   Left: effect size from the sound; a line-in picker.
8. **(DONE) Autopilot** (tempo menu -> Autopilot): a cue list is a pool
   of looks; every 4 / 8 / 16 / 32 bars on the beat clock another one -
   ranked calm / medium / big from the cues themselves (brightness,
   effects, strobing) and picked by how loud the room has been (Sound),
   the biggest straight away on a drop, round the list in order with no
   sound.  Next look now / Biggest now; AUTO on the tempo pill.
   Left: Pro DJ Link phrase data (verse / chorus) when a CDJ-3000 sends it.
9. **(DONE, first part) Spatial effects:** any running effect or
   movement can run through the room by where its lights are - left to
   right, right to left, from the stage out, back to the stage, up,
   down, centre out, outside in, round the room - instead of by light
   number: lights side by side move together, a gap in the rig is a gap
   in the wave (FX / Move tab -> the effect's "which way").
   Left: two-colour gradients across the room; bars / panels as pixels;
   images / video mapped onto the rig.
10. **(DONE) Step effects from your own looks** (FX tab -> Step effects):
    steps taken from the programmer (the look the selected lights have)
    or from palettes (the effect follows the palette), a time and a
    crossfade share per step, smooth / straight / snap, a spread round
    the cycle (by light number or, with a direction, by where the lights
    are), beat lock; saved with the show.  Gobos and other slot channels
    change at the middle of a fade instead of sliding through the wheel.
    Left: step effects inside cues and buttons; a key-frame shape editor
    for movement (phasers).
11. Desk tools.  **DONE:** highlight / solo (programmer -> Highlight, H /
    Shift+H), park dark or as it is (Fixtures ⋯; saved with the show),
    group masters (a fader per group next to the GM), macros (command
    lines in one go, one undo step; buttons), OSC in (Settings -> MIDI),
    the DJ-booth remote page (/remote.html), MIDI timecode (the timeline
    follows MTC from the desk's MIDI in: MTC in the timeline bar, with
    the timecode where the timeline starts), tracking or cue only per
    cue list (+ block, record cue only), move in black, cue actions
    (buttons, macros, other lists, timeline, tempo), preview / blind (3D only;
    preview-edit a cue, PREVIEW in the top bar - BLIND already means dry
    run here), Move tab rebuilt: Aim (follow me on the 3D floor, floor
    map), pan/tilt pad with nudge.  **Left:** LTC (audio
    timecode); OSC out.

## A3. Visualiser and venue editor

Research: Capture (truss library by manufacturer with end snappers and
fixture snappers along the tubes, rigging points in reports), Vectorworks
Spotlight (draw a truss by dragging a line to length; plots, legends,
auto-numbering, paperwork from the drawing), Depence R4 (photoreal real-
time beams, video mapping onto any surface, pixel-mapped lasers, motion
blur, VR).
12. **Many ways to make a room, drawing is only one.**  DONE: Arrange ->
    Room... (also Settings -> Venue and the start-up venue picker) with
    five ways: *Shape & size* (rectangle, L, T, U, octagon, round, fan /
    wedge; typed width, depth, ceiling and the cut-out; optionally a
    starter layout that fits the shape - DJ or stage at the stage end,
    dance floor, bar, trusses wall to wall - or only the walls, keeping
    the rigging); *Describe it* ("a 12 x 8 m club, bar on the left, DJ
    booth on a 40 cm riser": read offline, or by the AI when there is a
    key; it says what it read and what it guessed); *Template*; *Draw it*;
    *Floor plan*.  Every way previews the room first, drawn from what the
    engine will build.  Walls are drawn like a plan: right angles and a
    10 cm grid by default (Shift: any angle), the last wall lines up with
    the first corner, a typed length + Enter makes a wall exactly that
    long.  Copies of a truss or object N m apart (inspector -> Copies...).
    Left: a phone room scan; doors, pillars and balconies drawn in the
    drafting mode; a ceiling height per area; align / distribute for
    rigging.
13. **Rigging library.  DONE:** Arrange -> Rigging…: box (22 / 29 / 40
    cm), triangle and ladder truss, 48 mm pipe, truss poles, wind-up stands
    and base plates, each with a typical kg/m; shapes - straight, corner,
    frame, circle (arc pieces), goal post, pole, stand - at typed sizes,
    position, turn and **trim** (underside height).  A shape is one thing:
    moving a piece moves it, Trim… (inspector) hangs all of it, lights put
    on it spread round all of it, one automatic group.  Ends join when
    dragged within 40 cm of another piece's end.  **Rigging report**: each
    piece / shape, its own weight, the lights on it (their weight from
    the library file, typical for the kind where the file doesn't say),
    pick-up points and kg per point, warnings (point load, stand load,
    hung at the ceiling), a parts list in standard lengths, CSV.
14. **Look:** better haze volumetrics and gobo projections on surfaces;
    LED screens / video walls that play a clip or mirror a pixel map;
    shadows from the crowd and stage; lens flares at low angles; a
    "photo" render; first-person walk; camera presets per venue.
15. **Paperwork:** a light plot (top view with symbols, numbers,
    addresses) and a patch sheet as PDF, straight from the drawing.
16. **DONE:** the view goes to the front of the room it is showing (on
    load and whenever the room's size changes; it was placed once for the
    stand-in room - 24 m back from a 3 m room); the crowd follows each
    zone's area and thins evenly past 2,500 people in a big hall.

## A4. Programmer, per light type and brand

The pass walked every tab for one light of each type (see A0).  Next:
17. **Show only what the light can do**, everywhere: e.g. a wheel of whites
    (ADB ALC4) is labelled "Colour wheel" - call it white presets; a light
    whose file lists no wheel slots shows "guessed positions" - add
    **Teach the wheel** (step through it, name each colour, saved to the
    fixture); gobo wheels with pictures; prism / gobo rotation as
    direction + speed; macros and auto programs as named buttons; lamp,
    reset and fan under an "Advanced" fold.
18. **Mixed selections:** one section per kind ("6 movers: position,
    gobo; 12 PARs: colour") instead of the union of all controls.
19. **Brand colour matching:** LED colours differ between brands - a
    per-fixture colour calibration so "red" matches across the rig.
20. Lights with an unknown shutter "open" value (Warp M, COB blinder):
    Test this light learns it and stores it with the fixture.
21. Wording: "reaches all 1", "Other" in the programmer bar, and the
    strobe open / close row for dimmer-less lights.

## A5. A buttons screen (MagicQ execute-window style) and AI that programs

22. **A full-screen buttons page** (desktop and tablet): pages of tiles
    that can be buttons, faders (a group master, a playback), XY pads,
    speed / BPM tiles, cue-list tiles, and SFX tiles with MagicFX-style
    safety (arm key, E-stop, hold-to-fire); tile size, colour, icon,
    label and what it does all editable in place; lock the layout.
23. **AI that programs, not only commands.** "These lights should only
    hover around the dance floor and the DJ booth" becomes a program:
    a new *roam* movement that wanders smoothly inside venue zones (the
    aim solver per frame, so every light - hung, standing, on a pole -
    stays on the zone), assigned per group, saved as a look, a button or
    a cue, with a preview in 3D before it is applied.  Same for "the
    back truss chases red and white on the beat during the drop", "build
    me 8 buttons for this rig", "make a 32-bar build-up".

# Part B - done

## 1. Bug fixes (DONE)

- **(DONE) Laser "hold" sticks on; needs a second click to stop.**
  `web/app/fxpanel.js` `holdButton`: the Laser tab re-renders while the
  button is held (attribute reloads on every snapshot), the element under
  the finger is replaced, and the browser sends `lostpointercapture` to the
  document instead of the old element, so the release is never sent.  Also
  press/release are two independent HTTP requests to a `ThreadingHTTPServer`
  and can be handled out of order (a quick tap can end as "on").  Fix: one
  window-level pointerup/cancel that releases every hold, plus a per-owner
  sequence number the engine uses to ignore a stale "down".  Same pattern in
  `web/app/quickbuttons.js`.
- **(DONE) FX tab "Stop" and Looks tab "Record" flicker / sometimes unclickable.**
  `web/app/programmer.js` `refresh()` runs on every `lite` push and rebuilds
  `renderRunning()` and `renderLooks()` from scratch, many times a second; a
  click needs the same element from mousedown to mouseup.  Fix: keyed
  re-render (only when the content changes), as `renderOpenWarning` does.
  Sweep every panel for the same pattern.
- **(DONE, Move tab) Circle (and pan sweep, tilt bounce, figure-8) make movers go berserk.**
  `app/fxlib.py` `_circle` etc. write 0..1 of the FULL pan/tilt travel
  (540 x 190 deg after `logical16`) at one cycle per second by default.
  Fix: move around the head's current position with a `size` knob (default
  ~20% of travel), a slower default (~4 s per circle), and cap the rate by
  the model's calibrated motor time (`motion`).

## 2. Every channel gets a control (DONE: aux1..aux24, PARSER_VERSION 7)

Channels whose label maps to no role become `raw` and have NO programmer
control (held at 0).  Example: Chauvet Intimidator Wave 360, 33 ch (QLC+):
ch 4 Continuous Pan Rotating, ch 9 Built-In Auto Tilt, ch 28 Heads On/Off,
ch 29 Auto Programs, ch 30 Program Speed.  Fix: expose every non-maintenance
channel under its own name on the Beam tab, its capability ranges as chips,
recorded in cues (needs unique per-channel roles for unnamed channels).

## 3. Multi-head / multi-cell fixtures (DONE, see 15)

Channels that repeat per head collapse onto one role, so one control drives
all heads.  Example: Wave 360 has 4 heads - ch 5-8 tilt x4, ch 11-26 RGBW x4,
and ch 3 + ch 10 share `speed`.  Fix: sub-fixtures (#9.1..#9.4) selectable
alone or together, per-head values, and effects that run ACROSS the heads
(colour chase, rainbow, tilt wave).  Also covers zoned LED bars.

## 4. Group buttons and laser recording (DONE)

- The group chips (yours and the automatic ones): tap = select the group,
  hold = flash that group (`group_flash`, never stored or an undo step).
- Laser tab: **Record as a cue…** (the cue dialog) and **Make a laser
  button** (`quick_from_laser`: an on / off laser button from the laser
  look in the programmer, on the first empty slot).  Output stays
  armed-only.

## 5. Programming and cues redesign (DONE: items 10, 16, 8 Looks)

- Programmer: one attribute grid for every fixture type, plus a bar showing
  what is in the programmer, each with its own clear.
- Cues: a real cue list (number, name, fade, follow, drag to reorder, inline
  edit), current and next cue on every playback, Update / Merge / Replace
  when recording over a cue.
- Looks: palette tiles with a live colour/position preview.

## 6. Patch safety (DONE)

- (DONE, item 14) DMX map per universe with clashes in red and "Move #n".
- **(DONE) Change fixture type** (a light's ⋯ menu, or the selection):
  number, place, rigging, name, groups, cues and looks stay; the address
  stays when the new footprint fits, else the first free block (and it
  says so, and which cue values the new type has no channel for).
- **(DONE) RDM** (fixture list ⋯ → Ask the lights): over Art-Net
  (ArtTodRequest / ArtTodData / ArtRdm, app/rdm.py) each RDM light says
  its maker, model, DMX address, channel count and mode; the list shows
  it against the patch - ok, "different" (→ Change type…), "new" (→ Add
  it) and patched lights that didn't answer - and "Set address…" sends a
  new start address to the light itself.  Tested against a fake node;
  needs a node with RDM switched on.
- **(DONE) Ready? check** (Show ▾, or the command bar): no lights, DMX
  clashes, unnamed channels, cues pointing at unpatched lights, no cues,
  stay-on-the-floor off, lasers, blind output, send errors, never saved -
  each with what to press.
- **(DONE) Show versions and export:** every save that changes a show
  keeps the one before (last 20, shows/versions/<show>/); Show ▾ →
  Earlier versions… opens one (the current show is kept as a version
  too); Export downloads the show file.

## 17. More 3D models (DONE)

Of the ~2,400 fixtures in the bundled libraries, 102 (in their largest
mode) fell back to the generic model.  Now 55: two new types with their
own models - **Scanner** (a lamp housing with a pan / tilt mirror, the
beam off the mirror, 44 fixtures) and **Effect light** (flower / derby /
moonflower: a dome of lenses throwing four beams, turning while lit, 22) -
and name matching for lasers, hazers, studio COB lights (fresnel model),
strobes and follow spots that the channels alone didn't give away.  A
selftest keeps every light type paired with a 3D builder.  The rest
(dimmer packs, colour-changer accessories, "Other") stay generic.

## 16. Cue list (DONE)

- Recording over a cue: Replace / Merge (adds the programmer's changes,
  keeps the rest) / Insert before; the cue keeps its name and times unless
  new ones are typed ("Update" used to rename it "Cue 2" and zero its fade).
- Record dialog: "Where" (new cue at the end, or over cue N) + the mode.
- Cue list: drag a row by its ⋮⋮ to reorder, "Update ▾" (merge / replace
  / record before), live refresh (not while typing).
- **Effects in cues (DONE):** the effects running on the programmer's
  lights (or the selection) are recorded into the cue and leave the
  programmer; GO starts them, the next cue replaces them, release stops
  them; merge swaps an effect of the same kind; saved with the show.  The
  cue list shows them (⚡ Circle).
- **Part times (DONE):** a cue's level, colour, position or beam can have
  a fade of its own ("parts" in the cue list), e.g. colour snaps while the
  movers glide 4 s.

## 15. Multi-head lights (DONE)

A Wave 360's four tilts and RGBW cells: a value for one head is kept as
`red@2` / `tilt@3` (merge: LTP, a running effect wins; limits and invert
apply; a button setting the whole role covers every head); the frame
writer sends the k-th copy of a role its head's value.  set_colour /
set_attribute take `cell`; the programmer shows "Heads: All 1 2 3 4"
for such lights and sends only to the picked heads; cues and looks keep
per-head values; effects run "across each light's heads" (colour chase /
rainbow step head to head); a tilt wave across the heads on the Move
tab; a multi-head 3D model (heads on a bar, each tilting and coloured on
its own).

## 14. Patch safety: DMX map and clashes (DONE); LED extras (DONE)

- Every overlap between two lights' channels is found (the load-time check
  missed a light starting inside an earlier one: 20 + 13 ch vs 25), shown
  as a red warning above the list with "Move #n" (first free block), and
  in a DMX map (512 squares per universe, clashes red, per-light list).
- The Colour tab adds White / Amber / UV / Lime sliders on top of a pure
  picked colour, for the lights that have them.
- (Change fixture type and RDM: DONE, item 6.)

## 13. My venues, room shapes, rigging up front (DONE)

- "Venues ▾" in Arrange: save this venue (room, rigging, zones, objects,
  and - if you say so - the lights with their addresses and positions),
  open a saved one (room and lights, or room only), delete.  Stored under
  shows/venues/, never listed as a show.
- "Draw room shape" (any outline: L-shaped, custom) and "+ Truss",
  "+ Pole" (a vertical pipe), "+ Pipe" up front; the rest under "+ More".
- **(DONE)** Reshaping or shrinking the room brings rigging (and the
  lights hung on it) and objects back inside the new walls; a truss slides
  in whole, keeping its length, when it fits.
- **(DONE)** At start-up, an empty desk with saved venues asks "Where are
  you playing tonight?" (once per browser session).

## 12. Gig mode (DONE)

Every control at least 44 px (40 for chips / small), text 12 px and up,
row menus always shown: on by itself on phones and tablets (pointer:
coarse), a "Gig mode" switch in Settings for a laptop.  Phones keep a
Blackout button in the bottom bar on every tab.  Measured: 0 visible
buttons under 40 px on a 390 px phone and a 1024 px tablet (was 64 of 96
under 32 px on a laptop).  Also done: only the playbacks in use plus one
"+ Record a cue" slot; 3D labels are "#7" (full name on hover or for one
light) and never pile up; the status bar reads "BLIND - safe to program,
nothing reaches the lights" / "LIVE - the lights follow the console", the
numbers on hover.

## 11. Grouping for big rigs (DONE)

- Automatic groups (never stored, follow the rig): one per kind of light
  ("LED PARs 12", "Moving spots 6") and one per truss / pole / pipe they
  hang on (attached, else the nearest), plus "Floor".  Dashed chips above
  the list; tap selects, Shift adds.
- Lights of the same model patched in a row fold into one row
  ("LED PARty RGBW × 12 · 1.049-1.115"): tap selects them all, ▸ opens.
- Selection bar: "6 selected · Moving spots" and Odd / Even / Left / Right.
- Buttons can aim at an automatic group (and a split of it) and follow it.
- Shift-drag on the stage draws a box and selects the lights inside.

## 10. Quick fixes from the audit (DONE)

- ARM lasts 10 min, 1 hour or **until you disarm** (remembered); a laser
  switched on has no time cap of its own (the library's 600 s is ignored):
  ARM / KILL FX is its safety.
- Programmer tab dots use the same kinds as the "In the programmer" bar.
- Programmer tabs wrap onto a second row; fixture names show on two lines.
- Lights with no dimmer: the Level tab shows big On (open) / Off (closed)
  instead of a fader that does nothing.
- Colour-wheel-only lights: their wheel colours lead as big buttons; the
  picker waits behind "Pick any colour".
- (The laser hold sticking and FX Stop / Looks Record flicker were fixed
  earlier; the button editor's cut-off dropdowns are gone with item 9.)

## 9. Buttons as customisable as possible (DONE)

Done: every option in the button editor is a tap.
- **Lights:** all, the selection, a group or a type, or only the odd /
  even / left / right half of them.
- **Does:** flash (level, a colour or "keep their colour"), dim to a level
  (a ceiling, `cap` in the merge), colour, strobe (rate + colour), blackout
  these lights, an effect with its own knobs (arc / direction / size /
  speed for movements), **Mix** (any of level, dim, colour, strobe,
  blackout plus up to 4 effects), and **From the stage** (captures the
  programmer's values and the effects running on those lights).  GO,
  release, preset, SFX, fog, laser, ARM and KILL FX as before.
- **Behaviour:** hold, on / off, a timed shot; "turn off after N s"; radio
  groups ("one at a time with…").
- **Looks and layout:** its own tile colour; 8 named pages; drag to move,
  Alt-drag to copy, onto a button to swap; Duplicate.

- **Fades (DONE):** Fade in / Fade out (0.5-5 s) on flash, dim, Mix,
  From the stage and blackout buttons.  Brightness eases up on press and
  down on release (effects and movement keep running under it); pressing
  again mid-fade picks up from where it is.
- **Keyboard key (DONE):** one letter or digit per button, shown on the
  tile.  Holding the key holds a hold button; the console's own shortcut
  keys (B, X, A, L, C, R, O, I, D, G, F, /, ?, Space) can't be taken.

- **Tile size and icon (DONE):** Normal, Wide (2 across), Tall (2 down)
  or Big (2 x 2), using the empty spaces next to / under it (the editor
  says when they're taken; it then shows at normal size).  16 icons
  (bolt, sun, moon, star, heart, fire, snow, drop, music, strobe, spin,
  sparkle, eye, stop, up, down) drawn in the tile colour.

- **Button speed (DONE):** effect, My move and Mix buttons have a speed
  of their own (¼× to 4×, times the Speed master, or "Own speed" to ignore
  the master).  Live: scroll on the tile or right-click it; the tile shows
  the speed when it isn't 1×.  Changing it live is not an undo step.

- **MIDI note (DONE):** each button can take a note (0-127); "Learn"
  in the editor waits for a pad hit on the desk's MIDI input.  A note given
  to a button plays it ahead of the MIDI map file; note off lets go of a
  hold button.  The tile shows it (♪36) next to its keyboard key.

- **MIDI on this device (DONE):** Settings -> "MIDI on this device" (Web
  MIDI, Chrome / Edge): a controller plugged into the tablet or laptop the
  browser runs on plays the buttons; off until switched on, so a controller
  heard by the desk too never plays a button twice.  Learn listens to both.
- **Speed by touch (DONE):** in gig mode effect tiles show their speed
  badge; tapping it opens the speed menu (a long press would fight with
  holding a hold button).

**My moves (DONE):** "+ Save this as my move" on the Move tab; tap a move
to play it on the selected lights, tap again to stop; ⋯ to update it to
the knobs now, rename or delete; "My move" on a button follows the move
when it is updated; saved with the show.  As agreed: save a movement (shape, arc, direction, size,
speed, wave, where it is centred) under a name and pick it from a list on
the Move tab, next to a light's built-in programs.  It runs and loops on
any selected lights until stopped: not a cue, and not tied to particular
lights or a venue.  It can go on a button or into a Look.

## 8. UI/UX overhaul (DONE: items 9-17; audited with a 21-light mixed rig)

Found in the audit (same features kept, all of it re-laid out):

- **Gig-readiness:** 64 of 96 visible controls are under 32 px (GO / back /
  stop on faders, tabs, chips); 9 text sizes from 9.5 px; muted grey on
  near-black.  Target 44 px, text >= 12 px, 4.5:1 contrast; Blackout / GO /
  GM always on screen (the phone layout has no Blackout without a tab switch).
- **Type-aware programmer:** Level shows a dead dimmer fader for lights
  without a dimmer; Colour shows an RGB picker + 25 swatches on wheel-only
  lights; tab dots disagree with the "In the programmer" bar (shutter is
  Level in one, Beam in the other).  Lead with each type's own controls.
- **Clutter:** 10 empty "Record a cue here" playbacks; fixture names and
  button-dialog options truncated; programmer tabs cut off ("Setu");
  status bar shows raw Art-Net addresses.
- **Visualiser:** the crowd hides the rig and floor-level lights; head
  labels overlap; lights are tiny at default zoom.  Truss / pipe / tower
  (vertical when its ends differ in height) and a drawn room outline already
  exist in the engine but are hidden behind Arrange -> Draw room: make
  "Truss - horizontal / Pole - vertical / Pipe / Stand" first-class tools.
- **Venues:** no save / load per venue (a venue lives only inside a show).
  Add a venue library: room, rigging, positions and patch per venue.
- **Custom buttons:** the button dialog works but hides its options in
  truncated dropdowns; make "which lights" (selection, all, a group, a
  type) and "what it does" (flash, dim, colour, flash red / blue, strobe,
  movement, a saved look) tap choices.
- **Looks (DONE):** the Looks tab is named look tiles (colour preview,
  tags); "+ Save look" with a name and what to include (level, colour,
  position, beam, effects & movements, other); a look keeps its effects
  and its lights, so one tap with nothing selected brings the whole thing
  back (on a selection, it plays there); update / rename / make a button /
  delete from ⋯.  Palettes sit under a fold.  (DONE since: a search box
  once there are 7+ looks - name, colours, effects, kinds of light; a look
  keeps the kinds of light it was made on, so where its lights aren't
  patched it plays on every light of those kinds, and ⋯ → "Play on every
  LED PAR" does that on purpose.)
- **Effects:** ARM switches itself off after 10 min (`_a_fx_arm` default)
  and disarming stops the lasers; laser latch buttons cap at 600 s.  Offer
  "armed for 10 min / 1 h / until I disarm" and laser ON until stopped.
  Confetti (one load) wants hold-to-fire; CO2 quick 0.5 / 1 / 3 s shots.
  (DONE: ARM until disarmed, lasers uncapped, CO2 / flame / sparks hold +
  quick shots, confetti fires only after a 1 s hold, haze Off / Light /
  Medium / Thick.)

## 7. Move tab: floor-safe movement for every light, simple speed (DONE)

Built: one-tap spots and formations from the venue, nudge arrows, movement
tiles with direction / arc / size / speed / wave / lock, Speed master,
per-light range (Set top / bottom / left / right), dance-floor safe zones
for every mover + Check the floor, a light's own spin / program speeds,
laser safe zone (beam height / size), FX tab without movements, Tools ->
Setup, "In the programmer" bar with per-kind clear.  Since: Tap tempo on
the Speed master (120 BPM = 1×), "Make a button" and "Record as a cue…"
straight from a movement, "Make a button" on a saved move.  (The live
preview is the 3D view; no separate floor-map preview.)

Replaces movement spread over Position / FX / Tools with ONE "Move" tab.

- **Where it points:** top-down floor map; tap/drag aims the selection (aim-at
  from each head's venue position).  Old pan/tilt pad behind "Fine".
- **Stay on the floor (every mover, on by default):** draw the dance floor
  once (or use the venue zone); each light's pan/tilt safe zone is computed
  from where it hangs; "Check" points all movers at the floor's front / back
  / left / right edge to nudge any that are off.  Shown as a green zone.
  Effects FIT inside the zone (not clipped).  New lights get it on placing.
  Lasers get a laser safe zone (beams off audience eye level).  Static lights
  (PARs, bars, SFX) show "no movement".
- **Movement tiles:** Circle, Sweep, Bounce, Figure-8, Turn; knobs:
  direction (CCW / CW), arc (90 / 180 / 360), size (S/M/L), speed,
  together / wave; live preview on the map; Record as cue / Make button.
- **Speed, made easy:**
  - one Speed control per movement (very slow ~30 s per turn ... fast) with
    1/2x / 1x / 2x;
  - a light's own spin/programs as plain controls: Spin CCW / stop / CW +
    spin speed slow->fast (Jarvis maps into ranges like "1-127 CCW fast->slow"),
    built-in tilt speed and program speed as slow->fast sliders;
  - a Speed master fader next to GM: slows/speeds everything that moves
    (effects and lights' own spins), 1/2x, 2x, Tap tempo; also caps each head
    at its motor's real speed (motion calibration).
- **Tidy:** FX tab = colour/brightness effects only; Tools -> Setup; an
  "In the programmer" bar with per-attribute clear.

Build order: (1) engine - movement relative to aim point with size / arc /
direction / speed, fitted to safe zones (also fixes Circle), speed master;
(2) Move tab UI; (3) floor drawn once + auto safe zones + Check; (4) spin /
built-in speed controls, laser safe zone, tab tidy.
