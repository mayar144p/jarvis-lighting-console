# Backlog

Work agreed with the operator but not started yet, in the suggested order.
Each item notes the cause already found in the code, so whoever picks it up
starts from the diagnosis, not from scratch.

## 1. Bug fixes (next)

- **Laser "hold" sticks on; needs a second click to stop.**
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

## 3. Multi-head / multi-cell fixtures

Channels that repeat per head collapse onto one role, so one control drives
all heads.  Example: Wave 360 has 4 heads - ch 5-8 tilt x4, ch 11-26 RGBW x4,
and ch 3 + ch 10 share `speed`.  Fix: sub-fixtures (#9.1..#9.4) selectable
alone or together, per-head values, and effects that run ACROSS the heads
(colour chase, rainbow, tilt wave).  Also covers zoned LED bars.

## 4. Group buttons and laser recording

- A Groups strip: tap = select the group, hold = flash that group.
- Laser tab: **Record** (store the laser look - beams, mode, program,
  speed - as a cue) and **Make a laser button** (a quick button from the
  current laser look).  Output stays armed-only.

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

## 7. Move tab: floor-safe movement for every light, simple speed (DONE)

Built: one-tap spots and formations from the venue, nudge arrows, movement
tiles with direction / arc / size / speed / wave / lock, Speed master,
per-light range (Set top / bottom / left / right), dance-floor safe zones
for every mover + Check the floor, a light's own spin / program speeds,
laser safe zone (beam height / size), FX tab without movements, Tools ->
Setup, "In the programmer" bar with per-kind clear.  Still open: Tap tempo
on the Speed master, a live preview on a floor map, Record as cue / Make
button straight from a movement.

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
