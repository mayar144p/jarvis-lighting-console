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
- **FX tab "Stop" and Looks tab "Record" flicker / sometimes unclickable.**
  `web/app/programmer.js` `refresh()` runs on every `lite` push and rebuilds
  `renderRunning()` and `renderLooks()` from scratch, many times a second; a
  click needs the same element from mousedown to mouseup.  Fix: keyed
  re-render (only when the content changes), as `renderOpenWarning` does.
  Sweep every panel for the same pattern.
- **Circle (and pan sweep, tilt bounce, figure-8) make movers go berserk.**
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
