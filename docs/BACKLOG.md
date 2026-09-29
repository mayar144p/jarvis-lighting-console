# Backlog

Work agreed with the operator but not started yet, in the suggested order.
Each item notes the cause already found in the code, so whoever picks it up
starts from the diagnosis, not from scratch.

## 1. Bug fixes (next)

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

## 3. Multi-head / multi-cell fixtures (see 15: first part DONE)

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

## 5. Programming and cues redesign (mock-up first)

- Programmer: one attribute grid for every fixture type, plus a bar showing
  what is in the programmer, each with its own clear.
- Cues: a real cue list (number, name, fade, follow, drag to reorder, inline
  edit), current and next cue on every playback, Update / Merge / Replace
  when recording over a cue.
- Looks: palette tiles with a live colour/position preview.

## 6. Patch safety

- DMX map per universe (who owns which channels, clashes in red) with
  one-click "move to next free block".
- "Change fixture type" on a patched head, keeping position, groups and cues.
- RDM discovery (model / mode / address from the light) if the node supports it.
- Pre-gig "Ready?" check and versioned show backups / export.

## 16. Cue list (DONE, first part)

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

## 15. Multi-head lights (DONE, first part)

A Wave 360's four tilts and RGBW cells: a value for one head is kept as
`red@2` / `tilt@3` (merge: LTP, a running effect wins; limits and invert
apply; a button setting the whole role covers every head); the frame
writer sends the k-th copy of a role its head's value.  set_colour /
set_attribute take `cell`; the programmer shows "Heads: All 1 2 3 4"
for such lights and sends only to the picked heads; cues and looks keep
per-head values; effects run "across each light's heads" (colour chase /
rainbow step head to head).  Still open: movement effects across heads (a
tilt wave), per-head colours in the 3D view.  (DONE since: tilt wave
across the heads on the Move tab; a multi-head 3D model - heads on a bar,
each tilting and coloured on its own.)

## 14. Patch safety: DMX map and clashes (DONE); LED extras (DONE)

- Every overlap between two lights' channels is found (the load-time check
  missed a light starting inside an earlier one: 20 + 13 ch vs 25), shown
  as a red warning above the list with "Move #n" (first free block), and
  in a DMX map (512 squares per universe, clashes red, per-light list).
- The Colour tab adds White / Amber / UV / Lime sliders on top of a pure
  picked colour, for the lights that have them.
- Still open: change fixture type keeping position / groups / cues; RDM.

## 13. My venues, room shapes, rigging up front (DONE)

- "Venues ▾" in Arrange: save this venue (room, rigging, zones, objects,
  and - if you say so - the lights with their addresses and positions),
  open a saved one (room and lights, or room only), delete.  Stored under
  shows/venues/, never listed as a show.
- "Draw room shape" (any outline: L-shaped, custom) and "+ Truss",
  "+ Pole" (a vertical pipe), "+ Pipe" up front; the rest under "+ More".
- Still open: reshaping the room doesn't move rigging that ends up
  outside it; a venue picker at start-up.

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

## 8. UI/UX overhaul (audited with a 21-light mixed rig; mock-ups agreed next)

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
  delete from ⋯.  Palettes sit under a fold.  Still open: search and
  "any light of these types" for another venue.
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
