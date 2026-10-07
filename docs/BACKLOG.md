# Backlog

Work agreed with the operator.  **Part A is the open plan**, in the
suggested order; Part B (items 1-17) is DONE and keeps the notes on how it
was built.  Each item notes the cause already found in the code, so
whoever picks it up starts from the diagnosis, not from scratch.

# Part A - the plan (open)

## Next, in this order (agreed 2026-09-30)

1. **(DONE) Desktop only** (item A6 below): the phone / tablet features are out.
2. **(DONE) Review everything since PR #30** for bugs, then a full mock gig
   in the browser (patch, venue, cues, buttons, timecode, roam, blackout);
   fix what breaks.  Found: 7 review bugs (trim height, macro undo, bad OSC
   packets, colour-match throttle, 0-255 positions, camera reframe, colour
   calibration order); in the gig, the Add dialog kept a stale fixture after
   a search, and Record cue was only reachable from the Level tab (now on the
   programmer bar too).
3. **(DONE) Finish item 23** (AI that programs): 3D preview before a roam /
   effect is applied; "the back truss chases red and white on the beat
   during the drop"; "build me 8 buttons for this rig"; "a 32-bar
   build-up" on the timeline.
4. **(DONE) On the beat:** buttons / strobes that fire on the next beat; Ableton
   Link; effect size from the sound; an audio-input picker.
   Done:
   * Buttons fire on the next half beat, beat, 2 beats or bar. The desk
     setting is on the Buttons page; each button can override it (Fires).
     A press just after the beat counts as on it, and a tap between beats
     gives a hit on the beat.
   * Ableton Link, listen-only (app/link.py). Discovery and clock
     measurement give the tempo and the place in the bar. Tested against a
     fake peer on localhost, not yet against Live itself.
   * A sound link to the effects' size (movement size, dimmer depth).
   * An input picker in the Sound dialog, remembered per computer, falling
     back to the default when unplugged.
5. **(DONE) Effects:** two-colour gradients across the room; bars / panels as
   pixels; images / video mapped onto the rig; step effects in cues and
   buttons; a key-frame shape editor for movement.
   Done:
   * **Pixels** (app/pixels.py): every cell of a pixel bar or panel is a
     pixel at its own place along the bar; other colour lights are one.
   * **Gradient** (FX tab -> Paint the rig): 2-6 colours across the room
     in any direction (left-right, stage-out, up, centre-out, round),
     still or scrolling, or locked to the beat.
   * **Pictures / video**: a picture is shrunk in the browser (at most
     64 px a side) and kept with the show. A video plays in the browser
     and its frames go to the desk while it plays. Either is laid over the
     rig seen from the front or from above.
   * **Step effects, gradients, pictures and shapes in cues**: recorded
     and played again on GO. A button can run a step effect (on the beat
     too); "capture" buttons take running step effects.
   * **Shapes** (Move tab -> Shapes): key points on a square, smooth or
     straight, with presets (triangle, square, star, wave, zig-zag). They
     run with the Move tab's size / speed / direction / wave, and are kept
     with the show.
   * Fixed on the way: speeds below 1 given to the new actions were rounded
     to whole numbers (an integer clamp).
6. **(DONE) Instant buttons from moves** (asked 2026-10-01): when you are done
   setting a movement up on the Move tab (a roam over your own zones, a
   preset movement with its knobs, a shape), one press makes a button that
   plays exactly that, with no cue in between. The button is only for the
   lights selected when it is made, or the selected group (kept as the
   group, so lights added to the group later follow it). It keeps the
   zones / shape, speed, size, direction and beat lock. Today a roam can
   only become a button through a cue and a GO button. The Move tab's
   "Make a button" covers only preset movements and saved moves, and
   targets the movers rather than the group.
7. **(DONE) Follow speed in the engine** (asked 2026-10-01).  The Follow speed
   (Move tab -> Aim: instant / fast / medium / slow) already moves the real
   lights: the glide changes the pan / tilt sent on DMX, not just the 3D
   view.  But the glide is worked out in the browser:
   * a hidden or minimised tab slows its timers to about once a second,
     so the real lights move in jerky steps;
   * closing the page mid-glide leaves them where they are;
   * new positions go out about 14 times a second, not at the full DMX
     rate.
   To do: the browser sends only the target and the speed.  The engine
   glides the pan / tilt at the full 40 updates a second, whatever the
   browser does, the same for every screen.  Optional: use the fixture's
   own pan / tilt speed channel when it has one.
   Done (6 and 7):
   * Move tab -> Make a button (also in the Roam row) makes the button at
     once for the roam / shape / movement running on the selection. It is
     for the selected lights, or their group (kept as the group).
   * aim_at takes `glide`: the engine moves the aim every frame (glide
     state, a tick in the frame builder) and lets go when the lights are
     moved by hand. The live feed shows where the beams are.
   * Fixed on the way:
     - Slow roam (0.5) ran at 0.05: an integer clamp. Button seconds and
       strobe Hz were rounded the same way.
     - No way to stop effects without clearing the lights: an Effects chip
       (×) on the programmer bar and Stop mine. Clear and the chip leave
       cue / button / timeline effects playing, and each running effect
       says where it comes from.
   * The fixture's own pan / tilt speed channel: not used yet.
8. **(DONE) Show only what a light can do** (asked 2026-10-01):
   * The pan / tilt pad (Move tab, programmer) shows a light's real travel.
     That means its pan and tilt degrees from the fixture file (a 180° pan
     scanner, a tilt-only bar, a head with limited tilt) rather than the
     full square for everyone. Places it can't reach are greyed out, and
     the readout is in its own degrees. With several lights selected, the
     pad shows the range they share.
   * The 3D view, the aim tools (Follow me, the floor map, spots) and the
     movement effects respect the same limits.
   * No colour or movement controls on a unit that hasn't got them. The
     tabs already hide by channel for the whole selection, but the Laser
     and SFX panels can still show colour and move controls for a laser or
     CO2 that has no such channels, and a mixed selection shows controls
     some of its units can't use. To do: audit every panel per unit type,
     hide what a unit lacks, and say which selected lights a control won't
     reach.
   Done:
   * The pad: `pad_info` (axes, degrees from the fixture file, the part all
     selected lights can reach: their limits, the dance floor when it is
     locked).  The pad greys what they can't reach, labels its edges in
     the lights' degrees, reads out in degrees, and a drag stops at the
     limit.  A light that only tilts (a CO2 jet) gets a tilt-only pad and
     sends tilt only; one that only pans, pan only.
   * The Move tab: movements a light can't do are hidden (only Bounce for
     tilt-only; Sweep / Fan for pan-only).  Aim / follow / spots / roam
     need pan and tilt, so tilt-only lights get a note to use the pad.
   * Level / Colour / Beam / FX tabs count real lights only.  A laser's or
     effect machine's colour channels (a laser's red / green / blue diode
     channels) are on its Laser / SFX tab ("Other channels"); colour and
     level never reached them anyway.  A mixed selection keeps the tabs for
     its lights.
   * The output already clamps every value to a light's limits, so the
     3D view, cues and movements respect them too.
9. **(DONE) A real AI assistant** (asked 2026-10-01; replaces "more copilot
   commands").  Today the copilot is one shot: one message in, one list of
   allowed steps out, Apply.  Without a key it is the offline keyword
   compiler, which really is hard-coded.  It should work like an assistant:
   * **A loop, not one shot.**  Tools (read the rig / programmer / cues /
     DMX, set lights, record, make buttons, a 3D snapshot...) that the
     model chooses and chains itself, many steps a request.  It sees the
     result (DMX values, the 3D picture) and corrects itself ("the movers
     hit the ceiling" -> it checks the aim, re-aims, checks again).
   * **Feelings, not commands.**  "Make the drop hit harder", "more
     sunset", "calmer for the speeches", "this looks cheap, fix it".  It
     chooses colour / level / movement / timing and says why in a line.
   * **It talks back.**  It asks when unclear ("back truss or upstage
     truss?"), suggests ("a blackout hit on the 1?"), and answers about the
     show ("why is head 7 dark?" -> "parked since cue 3").
   * **It sees.**  The 3D view as a picture ("the left side looks empty");
     a dropped-in photo to match.
   * **It remembers.**  Your style, the venue, what you kept or undid
     ("warm white for speeches"), per show and per user.
   * **With the music.**  Optional: it follows tempo, sections and drops,
     suggests the next look, or runs an "AI operator" mode you can take
     over at any moment.
   * **Safety stays.**  3D preview first, one Ctrl+Z, never arms the
     output / fires pyro or CO2 / deletes without asking; the offline
     compiler stays as the fallback with no key.
   * Works with the Gemini key in `.env` (tool use); a stronger model is
     smarter.  The key stays in `.env`, never in the repo.
   Done (app/assistant.py, /api/console/assistant, the copilot chat):
   * A tool loop (up to 14 rounds, 40 actions).  Tools: look_at_rig,
     check_lights (level, colour, where each beam lands - a floor point and
     its zone - and the effects on it), music (tempo, sound, timeline part),
     do (the copilot's allowlist plus gradients, shapes, buttons, beat /
     space locks, highlight, tempo), ask (a question with answers to tap,
     the turn stops), remember / forget (notes kept in
     data/assistant_memory.json, given to it every turn).
   * The conversation is kept per screen; New chat starts over.
   * Preview in blind, Keep / Throw away; the whole turn is one undo
     step.  An AI failure half-way leaves nothing behind.
   * It sees: a small JPEG of the 3D view with each message (switchable),
     and a photo you attach, paste or drop.
   * Tested with a scripted model, with a stand-in OpenAI-compatible
     service end to end in the browser, and against Gemini itself (free
     plan) on a club rig: sunset on the dance floor, "the drop needs to hit
     harder", "why is head 2 dark?".  What that taught:
     - the free plan allows 15 requests a minute: Jarvis now paces its
       requests, and on "too many" waits the time the service names (up
       to 70 s) and tries again, else says plainly when to try again;
     - aim_at takes a zone ("Dance floor"), not only a mark;
     - `do` accepts the action's settings flat as well as in params;
     - a question is answered, not acted on: it checks, then says.
   * Fixed on the way: the pad's pad_info and venue_info became undo steps
     ("Undo pad info"); every read-only action is now outside undo.
   * **Sees its own changes:** the AI can ask to look at the 3D view after
     what it did (see_3d, up to 2 a request): the request pauses, the
     screen draws the preview and sends a picture, the AI carries on and
     fixes what looks wrong.  Tried with Gemini: it asked to look.
   * **AI operator** (copilot -> ▶ Run the show…): the AI runs the lights
     live with the music - a first look at once, then one change every
     8 / 16 / 32 bars on the beat clock and straight away on a drop (Sound),
     each one undo step, from a brief ("house night, warm, purple and
     amber") and what it did lately.  A green AI RUNNING pill in the top
     bar; touching the desk (a cue, a button, a fader, the programmer -
     not selecting or the venue) or "I've got it" hands the lights back at
     once, and a change it was still thinking about is dropped.  It never
     arms or fires effects.  Tried with Gemini end to end.
   * Later, separate (not planned yet): the AI changing Jarvis's own code
     on request - a branch + PR made by a coding agent, tests run, you
     review and merge, Jarvis updates itself (never patching the running
     app).
10. **(DONE) Venue / 3D:**
   * Arrange -> + More: **Draw a door** (two clicks along a wall: it goes in
     the nearest wall, as wide as drawn, turned along it), **Draw pillars**
     (click where each stands, floor to ceiling, until Enter / Esc),
     **Draw a balcony** (two corners; the deck high enough to walk under).
   * **Align / distribute:** Shift+click rigging and objects (blue marks),
     then line their middles up left / centre / right, back / centre /
     front, the same height, or spread them evenly across, in depth or in
     height.  A piece of a shape moves the whole shape; lights on a rig go
     with it; one undo step.
   * **A ceiling height per area:** + More -> Draw a ceiling area (lower
     under a mezzanine, higher over the floor).  The 3D cuts it out of the
     room's ceiling and hangs it at its height with a drop round the edge;
     rigging under a lower one comes down, drags snap to it, trims and
     "hang under the ceiling" use it, the rigging report warns against it.
   * Fixed on the way: a truss seen from above could not be clicked (the
     click fell between its rods, or picked the stage under it) - an unseen
     hull along each rig now takes the clicks.
   * **LED screens** show a clip or picture from this computer (inspector ->
     Shows -> A clip or picture from this computer…): sent to the desk,
     kept by content, played back in the 3D.  Links still work.
   * **Real gobos:** a light's own gobo pictures from its library file
     (QLC+ / OFL, 554 pictures in app/fixlib/gobos.zip) are projected on
     the floor and walls for the slot the gobo channel is in; lights whose
     file has no pictures keep the drawn patterns.
   * **Shadows:** the crowd, performers, objects and the stage block the
     brightest beams (Crowd menu -> Shadows; off on Fast quality).
   Left (later): real lens-flare streaks.
11. **Output extras:** LTC timecode; OSC out; Pro DJ Link phrase data.

Dropped for now (maybe later): the phone room scan.

## A9. Desktop app and a better visualiser (agreed 2026-10-07)

In this order.  Steps 1 and 2 are the plan; step 3 is optional / future.

### Step 1 - the desk as a desktop app (Electron)

**Status (2026-10-07): first part DONE** - `desktop/`, `run-desktop.bat` /
`.sh`, checked by `tools/desktopcheck.mjs`: engine hidden on a free
loopback port with a private key, clean stop on close, Show mode (3D /
programmer + fixtures / playbacks, each remembering its monitor), safe
keys, close asks, screens stay awake, the desk runs without 3D on a
computer whose graphics can't.  **The installer: DONE** - the
"Desktop app (Windows installer)" workflow builds `Jarvis Setup.exe` with
Windows' embeddable Python inside (the engine is stdlib-only, so no
PyInstaller), checks the built app with desktopcheck, and keeps it as a
private artifact; installed, data / `.env` / inbox live in `%APPDATA%\Jarvis`.
**Still to do:** the app updating itself, phones / tablets as a setting with
a pairing code, USB DMX / MIDI from the app, a Mac build.

Nothing is rewritten: the Python engine and the screens stay as they are,
wrapped in a program with its own icon.  The engine still listens on
localhost, hidden inside the app (the operator never sees an address).

- **One icon starts everything:** the app starts the engine (packed with
  PyInstaller, so no Python install) and stops it on close.
- **Windows across monitors** (the real fix for "everything on one page"):
  - the visualiser, full screen;
  - programmer + fixtures;
  - playbacks + buttons (a touch screen);
  - each remembers its monitor and size; a "Show mode" opens the set.
- **Safe during a show:** Ctrl+W / F5 / Ctrl+R / Backspace belong to the
  desk (nothing closes or reloads by accident); closing asks first; the
  computer and screen stay awake while the app is open.
- **Same graphics everywhere** (Electron's Chrome), so the 3D looks the same
  on every computer.
- **USB DMX interfaces and MIDI** directly; real menus; one installer that
  updates itself (Windows first, then Mac).
- **localhost, tightened:**
  - the engine answers only this computer by default;
  - the app and engine share a private key at start-up;
  - a free port is picked automatically;
  - phone / tablet remotes become a setting ("Allow phones and tablets")
    with a pairing code.
- Checks: the existing ones (selftest, uicheck, vischeck, brands) run
  against the app's windows too; a packaging check on Windows.

### Step 2 - the visualiser upgraded to WebGPU (same code, inside the app)

The three.js visualiser moves to its WebGPU renderer; nothing is thrown
away, and the same desk feeds it.

- **Haze and beams:** beams that light up real haze (volumes, soft edges),
  brighter where they cross.
- **Gobos** projected onto the floor, set, truss and people, sharp or soft
  with focus; prisms and frost visible on the projection.
- **Shadows** from every beam; reflections on shiny floors and set.
- **Quality settings** Low / Medium / High (and the current one as the
  fallback for weak laptops or no WebGPU), so 124+ moving lights stay
  smooth.
- Checks: vischeck and the fixture-debug fit check stay green; a
  frame-rate check with the 124-light rig on each quality.

### Step 3 (optional / future) - a separate pro visualiser (Unreal Engine)

Only if film-quality previs for clients is wanted, or a visualiser to offer
on its own: an Unreal Engine program that listens to Art-Net / sACN like a
real rig (so it also works with grandMA, Onyx...), with photographic haze,
light bounce and GDTF / MVR fixtures.  A separate project measured in
months (C++ / Blueprints), needs a gaming-class graphics card (RTX 3060 or
better), ~300 MB+ to install.  Godot is the lighter, free alternative if
the quality bar is lower.

## A10. Premium features (agreed 2026-10-07, after A9)

Suggested order: the operator's top picks first (1-4), then the rest.

1. **Hardware controllers with feedback** - ready layouts for Akai APC40 /
   APC mini, Novation Launchpad, Behringer X-Touch: lit buttons show what
   is on, motor faders follow the playbacks; plug in and play.
2. **Show templates + first-run tour + demo show** - wedding, club night,
   band, theatre, corporate: a ready rig, groups, palettes and buttons (a
   new show starts 80% done); a tour and a demo show so a new user sees
   lights moving within 30 seconds.
3. **MVR / GDTF import and export** - a plot from Vectorworks or Capture
   comes in with every light patched and placed; ours goes back out.
4. **DJ sync** - lock to Pioneer DJ decks / rekordbox (Pro DJ Link) for
   beat, bar and phrase, so the lights hit the drops without tapping.
5. **More universes and sACN in Settings** - sACN output already exists
   (`DMX_TRANSPORT=sacn` in `.env`); bring the choice and more universes
   into Settings -> Output, for bigger rigs and pro nodes.
6. **Customisable workspaces** - drag panels into your own layout per show
   or per user (busking, theatre, programming); switch with one key.  (Pairs
   with A9 step 1's windows.)
7. **Offline programming with a video render** - program at home, export a
   video of the 3D preview for the client.  (Pairs with A9 step 2.)
8. **Fixture library updates** - new lights arrive automatically; a
   "Request a fixture" button (it becomes an A11 report).
9. **Fine / coarse rotary encoders on screen** for precise pan, tilt and
   colour, like real desk wheels.
10. **Themes and a "show dark" mode** - an extra-dim, red-safe screen for
    dark venues; accent colours.
11. **Help built in** - hover any control for a short explanation (and a
    short video link).

## A11. Report a bug from the desk (agreed 2026-10-07)

So problems arrive with the evidence attached instead of typed by hand.

- **Where:** right-click a light (fixture list or 3D) or its menu ->
  "Report a problem with this light..."; Help -> "Report a bug" for
  anything else; "Request a fixture" (A10/8) uses the same path.
- **The form:** one line "what's wrong", ticks (DMX / 3D view /
  programmer / effects / crash).
- **Attached automatically (a "bug bundle"):**
  - the light's fixture file, mode, address;
  - its live DMX (channel report) and what the 3D thinks it does;
  - screenshots of the 3D view and the screen;
  - desk version, computer, recent log errors;
  - optionally (ticked, can be unticked) the whole rig / show, so the
    exact rig can be loaded for testing.
- **Privacy:** `.env` and the AI key are never included; the reporter sees
  the full list before sending.
- **Lands in GitHub Issues**, labelled (`light-bug`, `brand:...`,
  `model:...`), visible to the operator and Claude.
  - v1: the desk saves the bundle and opens a pre-filled issue in the
    browser (needs a GitHub account; drag the bundle in).
  - v2: a small relay service with a bot key creates the issue directly,
    so no GitHub account is needed (the key never ships inside the app).
- **Decided (2026-10-07):** the repo stays **private**; bug reports go to
  its own Issues tab (only the operator and Claude see them), with two
  forms in `.github/ISSUE_TEMPLATE/`: "Problem with a light" and
  "Something else is wrong".  The desk's report button (above) fills these
  same forms.  Before a public release with outside testers: a separate
  public "bug reports" repo + the v2 relay, so the code stays private.
  (History checked 2026-10-07: no `.env`, keys or `data/`; all bundled
  libraries are MIT / Apache 2.0, fine for a closed app with an
  "Open-source licences" page.)
- **Fixing:** the fixture-debug skill learns "fix issue #N" - download the
  bundle, load the rig, reproduce with the checks (DMX, safety,
  programmer fit, 3D), fix, add a test, link the fix to the issue.

## A12. Plug-and-play offline AI, with a switch to Gemini (agreed 2026-10-07)

Today the copilot uses one online AI from `.env` (Gemini, any
OpenAI-compatible service works).  Free online plans have daily limits;
an AI running on the laptop has none and works without internet.

- **Nothing to install.** The AI runtime ships inside the desk (llama.cpp
  server - tens of MB - or Ollama in portable mode), started in the
  background only when the copilot is used, stopped when the desk closes.
  No installer, no admin rights.
- **The model is one file** (e.g. `qwen3-8b.gguf`, ~5 GB) in the desk's
  data folder (or a drive the operator picks):
  - one click: "Download the offline AI (5 GB)?" with a progress bar,
    pause / resume, checked when done;
  - or an **"AI pack"** file - downloaded once at home or copied from a USB
    stick - dropped onto the desk, for venues with no internet;
  - "Remove" gives the space back.
- **Install once, use it - no further action:** the desk's installer has a
  ticked box "Include the offline AI (5 GB)"; the model downloads during
  the install, so the copilot works the moment the install finishes.
  (Unticked or offline while installing: the desk offers it later, or the
  AI pack.)
- **It survives updates:** the model file lives in the desk's data folder
  (with the shows and settings), not in the app's folder, so an app update
  never touches it; the small runtime is part of the app and updates with
  it.  Only a better recommended model is ever offered - "download?" - and
  the old one keeps working until the operator says yes.
- **The desk suggests the model** from the computer (RAM, graphics card,
  free disk): 8B for 16 GB laptops, 14B for 32 GB / a 12 GB graphics card.
- **The switch:** an AI button in the copilot (and Settings -> AI) with
  three positions, remembered:
  - **Online (Gemini)** - the AI in the code today, smarter, needs
    internet and has daily limits;
  - **Local** - unlimited, offline, slower without a graphics card;
  - **Auto** - Gemini first; when it hits its limit, has no internet or
    doesn't answer, the desk carries on with Local and says so ("Gemini
    limit reached - using the offline AI").
  The AI settings live in the desk (no `.env` editing); the key stays
  private as now.
- **During a show:** no downloads while the output is live; the local AI
  frees the graphics card when idle, so the 3D view keeps its speed.
- **Needs:** 16 GB RAM and ~6 GB disk for the 8B model.
- **When:** the switch and the fallback can come first (they also work
  with Ollama installed by hand); the bundled runtime and the one-click /
  AI-pack download come with A9 step 1 (the desktop app).

## A13. The AI runs the whole desk - and only the desk (agreed 2026-10-07)

Example the operator wants to just work: "add Chauvet something something,
put it on the truss in the middle, make it flash yellow and hover over the
dance floor at a slow speed, 20% for now."

Today (app/assistant.py): 67 desk actions, look / do / check, ask,
remember.  Placing on a truss, colour, flash / strobe effects and roaming a
zone at a speed already work.  Missing:

1. **Desk only.** Anything not about the show, lights, room or desk gets
   one polite line ("I only run the desk - try: 'warm wash on the
   movers'"): no essays, no general chat.
2. **Honest about each light, from the desk not the model's memory.**
   Before acting, the AI gets what every selected light CAN do (tilt, pan,
   colour mixing / wheel only / no colour, gobo, zoom, strobe - the same
   facts as the programmer fit check); it does what's possible and says
   what isn't: "Done on 8 movers; the 6 PARs can't tilt, so they stay."
   Actions that partly fail report per light, never silently.
3. **Every part of the desk as a tool:**
   - **(DONE 2026-10-07)** the fixture library: fuzzy search over the whole
     library ("chauvet something spot", typos fine), install, patch;
     several models fit -> ask with tap answers; a model not in the
     library -> say so and offer the nearest (never a near model in its
     place) - `find_fixtures` / `add_fixture` in app/assistant.py;
   - placement in words: "middle of the front truss", "left side", "both
     ends", "6 m high";
   - the room: add truss / pole, zones, marks;
   - groups, cues, playbacks, buttons, timeline, palettes: make, edit,
     rename, delete (delete asks first);
   - safe settings; save the show (asks first).
4. **Safety stays with the operator:** going live, ARM, firing confetti /
   CO2 / flame / lasers - the AI prepares it, the operator taps to confirm.
5. **A test list of ~50 real sentences** (like the example) run against the
   desk: after each, the result is checked (patched, placed on the right
   truss, flashing yellow, roaming the dance floor at 20%); impossible and
   off-topic requests get the right short answer.  Run with Gemini and
   with the local AI (A12) - small models need the tools kept simple.

## A8. Leftovers from the library sweeps (open, 2026-10-02)

Found by `tools/libsweep.py` (default modes) and `--all-modes` (all 8,093
modes).  None is a crash; each is one light type behaving oddly.  The
counts are modes, not lights.

1. **Combo lights with a laser (33 lights, 60 modes):** the laser module
   has no "on" channel the console finds ("nothing fires it").  This is
   a **safety** question, so no output is guessed: the laser part stays
   dark and ARM-gated until someone with the light confirms which
   channel and value turn it on.  Examples: American DJ Boom Box Fx2,
   Stinger, Fusion FX Bar 5; Chauvet GigBar IRC, COLORstrip Mini FX;
   Briteq Spectra 3D Laser 2CH; Cameo Wookie 3-channel modes.
   *Update (brand debug):* a laser whose beam is switched by a mode /
   colour channel with "Laser off / No beam / Blackout / Blanking" (or
   "No function" next to "Red laser switched on") now fires from its
   armed button and is held off otherwise (Laserworld RS400G, Stairville
   DJ Lase, All FX Bar...).  The rest still need a person with the light.
2. **Vari-Lite "Blue / Amber / Magenta Mixer"** (VL2402 Spot, VL3000
   Wash, 6 modes): subtractive mixers the console doesn't treat as CMY,
   so Locate comes out blue.  Needs a "mixer flag" kind of colour, like
   CMY with different filters.
3. **CMY-only light with no dimmer and no shutter** (Generic CMY Fader,
   3 modes): Full shows dark in 3D.  With no way to dim, "Full" on such a
   light should mean "flags out" and the 3D should draw it lit.
4. **Shutter flicker / random strobe / alternate never change the DMX**
   (Studio Due Shark 150C x4 modes, Mac Mah Mac Follow 1200, BoomToneDJ
   Strob LED 18 2ch, Pro-Lights Ra 2000Profile 44ch alternate / fan).
   The effect is offered but finds no range to move on that channel.
5. **Locate not white, from odd files** (still 18 modes):
   - ETC Source Four LED Series 2 Daylight / Tungsten HD "Direct": the file
     names two channels "Red" and one "Mint", so the white mix is off;
   - Blizzard Rocklite RGBAW 4-channel (red, amber, white only): Locate
     should use the white emitter alone, not red + white (pink);
   - Cameo CLBAR10RGBA 2-channel (dimmer + colour macro): slot 0 is not
     white; the macro's white slot isn't named in the file;
   - Varytec Typhoon 10 / 13 channel: Locate is cyan-ish (white + green +
     blue, red is called "Intensity red" in one mode only).
6. **Colour wheels whose slots have no names** (Vizi Beam 5RX, Martin
   RoboColor III / Roboscan 812, High End Trackspot HR, Lite-Works
   ColorChanger, PR Pilot 575): red and blue land on the same slot.  Fix
   per light with **Teach the wheel** (Colour tab); a library-wide fix
   would need the slot colours from the manufacturers.
7. **Not wrong, the sweep should skip:** Cameo P2 FC CCT modes are
   white-only (red and blue *should* give the same DMX); Chauvet
   ColorStrip Mini "Default" has one channel for "speed / red / fade".
8. **Tell the operator when the autosave was unreadable.**  The desk now
   starts empty and keeps the file as `autosave.broken.json` (batch 2),
   but only the log says so; a one-time notice on screen would be kinder.
9. **Lamps DMX can't close** (72 modes, mostly old scanners and HMI
   moving heads in short modes): no dimmer, no shutter, no colour LEDs.
   BLACKOUT can't darken them on the wire, so the 3D shows them lit in
   BLACKOUT too (true to the real light).  The Ready? check lists them
   before doors.  (Found 2026-10-02 by the libsweep Blackout check.)
10. **Lights whose only on / off is a colour-macro channel** (1- and
    2-channel modes): Full, Out and Locate now use a slot named
    "Blackout" when the file has one; a file that names nothing leaves the
    desk guessing.  Worth a "Test this light" question for these.
    Example from the brand debug: Showtec Dynamic LED v3 "d-P2" (Programs:
    0 "No function", then Red, Green...; a strobe with no ranges) - the
    3D shows it lit white at rest; the real light is probably dark there.
    Same with Varytec LED Derby ST 4-channel (colour channel: 0-5 "No
    Function", then Red, Green, Blue, White): on an LED light "No function"
    at 0 is likely dark, on a lamp light's wheel it is open white - the
    file can't tell which, so ask once with the real light.
11. **Combo bars drawn as one thing** (Stairville All FX Bar: PARs,
    derbys, strobe and a laser on one bar): the desk treats it as a laser
    (so the laser part needs ARM), and the 3D draws a laser projector.  A
    combined model - a bar with its lenses and a laser fan - and Full /
    colour reaching its LED part would match the product.  Found by the
    fit check (`tools/vischeck.mjs --brands`).

## A7. Operator's debugging list (DONE, 2026-10-01)

- **Button from your moves:** "Make a button…" on the programmer bar and
  the Move tab captures what the picked lights do (aim, movement, colour),
  frees the programmer and turns the button on.  While it is on, it
  overrides everything and the programmer can't change those lights'
  movement ("turn the button off to change them"; a strip on the
  programmer says which button holds them, with Turn it off).  The
  movement keeps running round the captured aim.
- **Aim speed in 3D:** the visualiser follows the desk's glide (the Aim
  speed chips work for spots, formations and Follow); a first aim glides
  from home instead of jumping.  Instant / Fast are still limited by the
  3D motor model, as the real motors are.
- **Colour names, not Colour 1..6:** wheel slots get names from their
  colour (and a colour from their name); unnamed wheels say so and offer
  "Name the colours…".
- **Lights that didn't change colour:** default modes prefer RGB modes over
  1-channel macro modes; amber / UV / lime / white mix into the 3D colour;
  orphan fine channels (Lixada) fixed; Already-patched lights are re-read
  at start-up (PARSER_VERSION 11); a light in a poor mode shows "Use the …
  mode".  Sweep of 1,624 library lights: 89 failures -> 7 (truly unnamed
  wheels, now explained).
- **Built-in programs** (auto / sound / program channels) now show in 3D
  (hue wander, spin) and are labelled on the light.
- **Lasers:** standing lasers aimed up and back (now over the crowd); lasers
  defaulted to 1-channel auto modes or had no output channel (mode-switch
  "laser on/off" now found): fireable lasers 21 -> 54 of 81.  27 still
  have no channel that can be fired safely.
- **Locate vs Highlight:** Locate now centres pan / tilt and takes the
  lights back from effects you started; a cue's or button's effect keeps
  playing and Locate says Highlight shows them anyway.
- **FX tab:** bigger cards and rows; every running effect has Speed (and a
  movement Size) as Slow / Medium / Fast, − / +, ½× / 2× and a typed
  number, changed live with no restart or jump (`fx_tweak`).  The Move
  tab's speed is the same (seconds per turn).
- **Buttons screen:** the dock is taller in Buttons mode, its top edge
  drags to resize (remembered per mode, double-click resets), empty rows
  under the last button are hidden while playing, bigger text.

## A6. Desktop only (DONE)

Done: tablet.js, the manifest and home-screen icons, the remote page,
the Settings rows, gig mode on by itself for touch screens and every
narrow-screen layout are gone; the window is at least 1280 px wide (the
page scrolls below that).  Also fixed while there: multi-head lights
(Wave 360) now follow per head - the heads picked alone, or fanned out.


Jarvis runs on a desktop / laptop screen only.  Remove what exists for
phones and tablets:
- web/app/tablet.js (screen wake lock, "Install as an app", the iOS
  add-to-home-screen hint) and its Settings rows (Keep this screen awake,
  Install as an app); web/manifest.webmanifest, the manifest link and the
  home-screen icon sizes only it uses.  Keep the reconnect re-sync that
  lives there (it matters on a laptop's Wi-Fi too) - move it into main.js.
- Gig mode turning itself on for touch screens (`pointer: coarse` in
  main.js); the Gig mode switch itself stays for a laptop at a gig.
- The phone / small-screen layouts in app.css (the max-width 820 / 700 /
  560 / 520 / 480 / 380 px rules, the phone bottom bar with its Blackout)
  - with a sensible minimum window width instead.
- The DJ-booth remote page (web/remote.html, web/remote/, the Settings
  link to it): it is a phone page.  OSC in stays (Companion, QLab and
  other desktop apps use it).
- Tablet wording ("on phones and tablets", "a tablet at the DJ booth",
  "turn auto-lock off in the tablet's settings", touch hints) in tooltips
  and help.
- The selftests for the removed parts (wake lock / install / remote page),
  and the tablet notes in item 3 of A1.
Keep: pointer events (they work with a mouse), the floor map on the Move
tab (useful on a desktop), full screen for the buttons page.

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
   Left: nothing (Ableton Link and buttons on the beat: done, plan step 4).
7. **(DONE) Sound-reactive control** (tempo menu -> Sound): a screen
   listens (microphone / line in; https or the desk computer itself) and
   sends loudness, bass, mids and highs (each with its own automatic
   gain), beats, a tempo estimate and drops (the bass back hard after a
   breakdown) ~25 times a second.  Links: a sound (or the beat as a
   pulse) moves everything's, a group's or some lights' brightness, or
   the effects' speed, with depth and sensitivity; triggers: each beat /
   bar / drop (every Nth) presses a button; the room's beat can be the
   tempo.  With nothing listening nothing is dimmed.  Saved with the show.
   Left: nothing (effect size from the sound and the input picker: done).
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
   Left: nothing (gradients, pixels and pictures / video: plan step 5).
10. **(DONE) Step effects from your own looks** (FX tab -> Step effects):
    steps taken from the programmer (the look the selected lights have)
    or from palettes (the effect follows the palette), a time and a
    crossfade share per step, smooth / straight / snap, a spread round
    the cycle (by light number or, with a direction, by where the lights
    are), beat lock; saved with the show.  Gobos and other slot channels
    change at the middle of a fade instead of sliding through the wheel.
    Left: nothing (in cues and buttons, and shapes: plan step 5).
11. Desk tools.  **DONE:** highlight / solo (programmer -> Highlight, H /
    Shift+H), park dark or as it is (Fixtures ⋯; saved with the show),
    group masters (a fader per group next to the GM), macros (command
    lines in one go, one undo step; buttons), OSC in (Settings -> MIDI),
    the DJ-booth remote page (removed with Desktop only), MIDI timecode (the timeline
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
    Doors, pillars, balconies, ceiling areas and align / distribute:
    done (plan item 10).
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
14. **Look.**  Already there: haze beams, gobo projections on every
    surface (the surface shader), a head-on lens glow, saved views per
    venue (Views -> Save this view).  **DONE now:** LED screens show the
    lights (a live mirror: every light's colour as a tile, cells of a bar
    each their own), a clip or a picture by link, or nothing (inspector ->
    Shows); Views -> Take a photo (the view re-rendered at 4K, PNG);
    Views -> Walk around (eye height, W A S D / arrows, Shift runs, drag to
    look, stays inside the walls, Esc).  Shadows and clip upload: done
    (plan item 10).  **Left:** real lens-flare streaks.
15. **Paperwork.  DONE:** Show ▾ -> Paperwork… (/plot.html): the light
    plot (the room from above - walls, stage, zones, objects, marks,
    rigging with names and trims - a symbol per kind of light with its
    number and universe.address, a legend with the models, a 2 m scale,
    a title block: show, venue, lights, rigging load, date, power; a
    deep room turns on its side to fill the page), the patch sheet (by
    universe and address: channels, name, maker / model / mode, kind,
    what it hangs on, x y z, kg and W) and the rigging report with the
    parts list.  A4 landscape; Print / Save as PDF.
16. **DONE:** the view goes to the front of the room it is showing (on
    load and whenever the room's size changes; it was placed once for the
    stand-in room - 24 m back from a 3 m room); the crowd follows each
    zone's area and thins evenly past 2,500 people in a big hall.

## A4. Programmer, per light type and brand

The pass walked every tab for one light of each type (see A0).  Next:
17. **Show only what the light can do.  DONE:** a wheel of whites is
    "White presets"; a colour wheel / gobo / prism the file doesn't
    describe has **Teach the…** (step through it on the real light, name
    each position, a swatch for colours; saved with the fixture for every
    light of the model; forget); gobo / prism / a light's own rotation
    channels as ↺ ■ ↻ + slow-fast (from the file's named ranges);
    lamp / reset / fan / display / control channels under an "Advanced"
    fold; named ranges (macros, auto programs) were already one-tap chips.
    Gobo pictures: done (plan item 10).
18. **Mixed selections.  DONE:** the Beam tab shows one section per model
    ("3 × Moving Head · colour, position, beam"; "4 × LED PARty · more
    channels") with that model's controls, sent to those lights only
    (set_attribute / set_attr_range take `heads`).
19. **Brand colour matching.  DONE:** Colour tab -> Match colours across
    brands…: show white / red / amber… on the selection next to a
    reference light and take each emitter down (40-100%); kept with the
    fixture (every light of that model and mode, not an undo step),
    applied as the frame is written (cues, effects, buttons - 16-bit
    too); the 3D view keeps the colour asked for; reset.
20. **DONE (already there):** lights with an unknown shutter "open" value:
    the Level / Beam warning and Test this light find it on the real
    light and `remember_open` stores it for every light of that model.
21. **DONE:** "this light mixes any colour" / "all 7 mix any colour";
    the programmer bar's "Other" says what it is (Laser, Effects, Own
    channels); dimmer-less lights: "the fader dims their colour. Their
    strobe channel: Open - light on / Closed - dark".

## A5. A buttons screen (MagicQ execute-window style) and AI that programs

22. **A full-screen buttons page.  DONE:** Buttons -> ⛶ Full screen (the
    whole screen, browser full screen too); tiles can now also be
    **Fader** (grand master, Speed master, a playback, a group master -
    vertical in a tall tile, live), **XY pad** (pan / tilt of the tile's
    lights), **Tempo** (BPM, tap on the beat), **Cue list** (cue now and
    next, tap = GO, ◀ back) and **E-stop** (every effect and laser off,
    disarmed); with the existing ARM / hold-to-fire SFX buttons, sizes,
    colours, icons, pages and in-place editing.  **Lock** (per device)
    hides Edit so nothing can be moved.
23. **AI that programs.  DONE (roam):** "These lights should only hover
    around the dance floor and the DJ booth" becomes a program - the new
    *roam* movement: every mover on its own smooth path inside the zones
    (several zones share the lights out; "the crowd" = standing / seating
    / dance floor), aimed each frame by the aim solver from where it hangs
    (truss, pole, floor: 96 of 96 sampled beams on the zones), Speed
    master and beat lock, recorded in cues; Move tab -> Roam (zones,
    slow / medium / fast); the copilot knows it, offline too ("hover /
    wander / roam / move around the …").  Fixed on the way: the 3D view
    eased pan / tilt from a standstill on every update, so heads following
    a moving target (roam, the floor map, an XY tile) hardly moved.
    **DONE (the rest):**
    * **Preview in 3D** on a copilot plan: blind now holds back the effects
      and roams started in it as well as the programmer (the 3D view shows
      them). Keep hands it all to the rig, Throw away drops it. A cue's,
      a quick button's or a timeline clip's effect still plays live.
    * **"The back truss chases red and white on the beat during the drop"**:
      `chase_colours` makes a step effect (each light takes the next colour
      every step, neighbours apart) and runs it now, or as a clip over a
      named part of the song. A rig is found by what people call it (back =
      rear = upstage, nearest word first), and the copilot target is
      "rig <name>". Timeline effect clips run step effects and lock to the
      beat.
    * **"Build me 8 buttons"**: `quick_defaults` takes a count (the most
      useful first: flash, strobe, blackout, colours, a chase, movement), a
      focus (strobe / colour / effects / movement) and the first free page.
    * **"A 32-bar build-up"**: `timeline_build` adds a dimmer chase at 8, 4,
      2 and 1 beats a round, sixteenth-note pulses in the last bar, and the
      master going from half to full. It ends at the drop.
    * Fixed on the way: Rainbow ignored *spread*, so "rainbow across the
      rig" was one colour everywhere. The 3D view's colour easing restarted
      on every update, so on a slow GPU a fast-changing look (rainbow,
      chase) stayed the colour it started.

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
