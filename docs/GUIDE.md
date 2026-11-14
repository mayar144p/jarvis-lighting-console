# Haze — user guide

How to use the desk. Getting it running is in the [README](../README.md)'s quick start.

## The desk

| Region | What it does |
|---|---|
| **Top bar** | show name, output state and **GO LIVE**, undo/redo with the last action named, command palette (<kbd>Ctrl</kbd>+<kbd>K</kbd>), copilot, patch lock, settings, help |
| **Fixtures** (left) | the patch as a table; filter, add lights, select by click / ctrl-click / shift-click, select similar. **Change a group after it exists:** a right-click on its folder adds the selected lights, takes them out, makes it exactly the selection, renames or deletes it; a light's own menu has *Add to “group”* / *Take it out of “group”*. Buttons, faders and sound links that use the group follow. **Drag and drop:** drag a light (or the selected lights, or a folded type) onto a group's folder to put it in; drag it out of a group's folder onto the list to take it out; from one group onto another moves it (hold Alt to keep it in both); *＋ New group* appears while you drag. **Your groups are folders**, like a code editor's file tree: ▸ opens one, clicking its name selects all its lights, hovering it rings them in the 3D view (double-click frames them). Lights in no group stay in the list below |
| **Stage** (centre) | the 3D venue; views and saved views, frame selection, house lights, haze, crowd, zones, **Arrange** mode, full screen, and a *now playing* strip |
| **Programmer** (right) | tabs for what the selection can actually do: Level (and shutter open/close for lights without a dimmer), Colour (HSV picker, swatches, hex, white temperature, wheel steps), Position (pan/tilt pad, aim in degrees, **aim at a spot** or a performer mark, home), Beam (strobe speeds, gobo/prism steps, zoom/iris/frost, every attribute), FX, Looks (palettes and presets), Tools (fan, arrange, limits). The busy tabs come in parts, each remembered: **Position** - *Point* (aim, spots, the room map, the pad), *Move* (movements, A → B, waves; direction / arc / lock under *More*), *Limits* (stay on the floor, each light's range, laser safety); **Beam** - *Quick* (one-press strobe, gobo, prism, zoom...) and *All channels*; **FX** - *Effects*, *Running* (with how many run) and *Step & paint* |
| **Bottom panel** | three modes: **Faders** (cue stacks with GO / back / release, crossfade time, a per-cue fade / hold / follow timeline), **Buttons** (quick buttons) and **Timeline** (the show against the music), plus the grand master and BLACKOUT |
| **Status bar** | output target and rate, network, feed health, last save |

It is made for a desktop or laptop screen (1280 px wide and up).

### Three modes: Set up, Program, Live

The switch in the top bar shows the screen for the job in hand; the timeline,
cue lists and buttons aren't all there at once.

| Mode | On screen | For |
|---|---|---|
| **Set up** | the fixture list, the 3D view and the **Set up** hub (no faders: nothing plays yet) | the lights (add, import a plot or CSV, DMX map, address them all), the room (arrange, shape, rigging, describe it in words), the connections (output, nodes, RDM, DJ gear, MIDI / OSC / Stream Deck, Hue, a backup desk), the checks (**venue check-in**, test a light, ready check). Each step says how it stands (6 lights patched · no clashes) |
| **Program** | the fixture list, the 3D view, the programmer, and the faders / buttons / timeline below | looks, cues, palettes, effects, buttons, the timeline and songs |
| **Live** | the 3D view, the **Live** view and the buttons, master and BLACKOUT | running the show: what plays now and what comes next |

A new, empty show opens in Set up; otherwise the desk opens as you left
it. Each mode remembers the workspace you last used for it (the workspace
button still arranges panels, sides and widths; your own workspaces keep
their mode).

**The Live view** shows the song playing (or the track on the master deck),
the part it is in - *Drop 2* - with a bar running to the next part and
*Breakdown in 6 bars*; the tempo with the beat and Tap / Phrase starts now;
the autopilot (start, next look, or "covering between songs"); and the hit
buttons (strobe, flash, blinder, fog... from the Buttons page) big enough to
hit in the dark. A hit holds while your finger is on it.

**The programmer follows the rig:** with nothing selected it shows the tabs
the patched lights can use at all - no moving heads, no Position tab.

**What is connected** shows in the status bar as small chips - CDJs ×2 ·
master 1, Link, MIDI clock, a controller by name, OSC in (Stream Deck,
Companion), Hue, Backup desk, Songs follow decks - green when well, amber
when listening but nothing is heard. Click one to set it up. Nothing
connected, nothing shown.

### Venue check-in

A show made for one rig meets another at each venue. **Set up → Venue
check-in…** asks the lights here (RDM) and lines them up against the show:
- lights found as patched;
- **lights to swap in**: a light of the show that didn't answer, and a light
  of the same kind found here (*#5 LED PAR isn't here; the SlimPAR at 1.033
  is a PAR*) - **Use it** makes it take #5's place: its type and address
  change, its number, place, groups, cues and songs stay (one Ctrl+Z);
  another found light can be picked instead;
- patched here but a different light (change it to the one that answered);
- the show's lights not heard, with no light of their kind (park dark);
- other lights here (add them).

RDM needs a node with RDM on and lights that support it; without it the
check-in points to the DMX map and *Change fixture type*, which do the same
by hand.

### The venue

The stage sits in a real room: floor, walls, ceiling or an open roof, a stage
deck, zones (dance floor, bar, seating, VIP, DJ, FOH) and the rigging lights
go on. A fresh desk opens in a **club**. **Settings → Venue** swaps in a
template (club, small club, warehouse, concert stage, theatre, ballroom,
outdoor stage) and resizes the room.

* **House lights** (bottom right of the stage) make the room visible with the
  rig dark. Walls between you and the stage are cut away automatically.
* **Exposure** (next to House and Haze) brightens or darkens the picture, the
  way a camera's exposure does; the lights themselves don't change.
  Double-click it to put it back to the middle.
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

### Haze in the 3D

The **Haze** slider (next to House and Exposure) sets how thick the haze is,
so the beams show in the air.

* **Auto**: the haze follows the hazers in your rig. While the desk runs a
  hazer the room fills (faster in a small room), thickest in a cloud that
  rolls out from the hazer at first; when it stops, the cloud thins in
  seconds and the room's haze settles over minutes. The slider is then the
  most haze there can be, and its label shows how much there is now. Off
  (the usual for programming), the slider sets the haze. A rig with no hazer
  keeps the slider's haze either way.
* **Haze area** (Arrange → + More): a part of the room where the haze is
  thicker - under a balcony, round the DJ, by the smoke machine. Nothing
  solid: the beams show it. Its box shows only while arranging; **Thicker**
  sets how much.

### The sky over an outdoor stage

A venue with no roof (the **Outdoor** template, or a room with no ceiling)
has the sky over it. **Settings → Venue → Sky**: night with stars, a city
night (an orange glow, few stars), a sunset, overcast or daylight - or your
own 360° photo of the real place (a wide panorama, twice as wide as it is
high). The sky lights the field too: at night the beams own the dark; in
daylight the lights hardly show, which is worth seeing before an early slot.
**Turn it** moves where the sun sets, or which way your photo faces.

### Projectors

Arrange → **+ More** → **Projector (throws a video or picture)** puts a video
projector in the room, at the back pointing at the stage. Select it to set
what it shows - the same choices as an LED screen: the rig's colours, a clip
or a picture from this computer or a link, or live video (NDI) - and its
**Lumens**, **Throw** (the distance over the picture's width: at 1.6 a
picture 3.75 m wide from 6 m), **Tilt°** and the picture's shape. Turn it
with Rotate. Its picture lands on whatever is in front of it - a cyc, a
drape, the stage, the crowd - with the shadows of what stands in the way,
shows in haze as a beam of its own colours, and counts in the light map.

### Arrange mode

*Where:* Set up → Arrange the room

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
* **Light.** Beams are driven by the live output: colour, intensity, zoom,
  pan/tilt, gobo and strobe, lighting the floor, the flown truss and the
  performers, with bloom and optional haze. A lens that looks straight at
  the camera draws a thin flare streak, as a camera sees it.
* **Beams in haze** (Medium and High quality) are light in the air, worked
  out along your line of sight: brightest at the lens and fading as the beam
  spreads, brighter when it points at you, with the gobo and the prism making
  real shafts that match the pattern on the floor. A beam stops exactly where
  it meets anything solid - a person, the truss, a balcony, a wall at any
  angle - and the brightest lights leave a shadow in the haze behind what
  stands in them. The haze drifts slowly. **Low** keeps the simpler beams
  for older laptops.
* **Real light output.** How bright a light's beam and pool are comes from
  how much light it really puts out (lumens) and its lens: a small LED PAR
  next to a big spot looks like it does in the room, and the same light in a
  narrower beam makes a brighter pool. The numbers come from the fixture's
  file when it has them; otherwise the desk estimates from its wattage and
  kind. To correct them from the maker's data sheet, right-click the light →
  **Light output in the 3D…** (it applies to every light of that model;
  **Back to the fixture file** undoes it).
* **Every light lands.** A rig of hundreds of lights puts every pool on the
  floor, the walls, the truss and the crowd - not just the brightest few.
  The brightest 16 (High; 6 on Medium) cast shadows: soft from washes, crisp
  from spots and profiles. Their shadow pictures are only redrawn when a
  light moves, so a rig holding its position costs nothing extra.
* **Light bounces.** The brightest pools light the room back in their
  colour times the surface's: a red wash on a white wall turns the floor in
  front of it red; a black floor sends little back. LED screens light what's
  in front of them with what they show.
* **CO2, fog and confetti in the light.** A CO2 jet fired through a red beam
  glows red, fog takes the colours of the beams in it, confetti flashes in the
  colours it falls through.
* **Mirror floors.** A black dance floor, tiles or polished concrete shows
  the beams, the lenses, the truss and the screens reflected in it - sharp in
  a black floor, softer in concrete, stronger at a glancing angle than looking
  straight down (Medium and High; the floor is set in Settings → Venue).
* **True colour.** The colour you pick on the desk is the colour on the
  screen, dim or bright: bright red stays red, green stays green. Only far
  brighter than the screen's white - a lens, the core of a narrow beam - does
  a colour run to white, as it does to a camera and to the eye.
* **Strobes at their real speed.** While a light strobes the 3D draws every
  picture the screen can show and counts the flashes, so a 10 Hz strobe
  flashes ten times a second. Faster than half the screen's pictures a second
  (30 on most screens) it flickers as fast as the screen can.
  **Settings → Screen & 3D → Calm flashing** shows strobes as a slow shimmer
  instead, for anyone who mustn't watch flashing light; the real lights
  strobe as programmed.
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
  something changes. The 3D starts at **High** quality (sharpest, sixteen
  beams cast shadows). On an older laptop, **Settings → Screen & 3D → Quality:
  Medium** lowers the resolution when frames run long and wins it back when
  there is headroom; **Low** drops the shadows.

Views: <kbd>1</kbd>–<kbd>6</kbd> (front, house left, house right, back, plan,
whole room), <kbd>F</kbd> to frame the selection.

### The light map (how much light lands where)

The 3D view's **>>** menu → **Light map (lux)** paints every surface and
person by how much light the rig puts on it, in lux: dark blue for none,
then blue, green, yellow, orange and red to pink for the brightest. The key
on the left says how many lux each colour is, and thin lines mark each step,
like the lines on a map. Point at the floor, a wall, the stage or a person
to read the lux there. It counts each light's real output, its beam and
gobo, the distance and the angle, and anything solid in the way - not the
house lights. The beams in the air go while it's on; the × on the key (or
the menu again) turns it off.

A rough guide: a club dance floor 20-200 lux, a bar 50-150, a stage wash
500-1500, a face on camera 300-1000 (TV 1000 or more).

With an AI key, ask the copilot "is the DJ lit enough for the stream?": it
measures the stage, a face on it, the DJ and each zone, and can set the
lights to suit.

### Quick buttons

*Where:* Buttons (bottom left)

**Buttons** in the bottom panel is a grid of instant buttons on up to 32
named pages (double-click a page in Edit to name it), like a MagicQ execute
window. Each one is **hold** (on while pressed),
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
| Heading | words only, over a row of buttons; pressing does nothing |
| Go to page | shows another page |
| Folder | opens a page with a ◂ Back to the one you came from |

A **Fader** tile can also be **Some lights**: pick the lights (the
selection, a group, a type, odd / even...) and whether the fader is
*their brightness* (up from dark, over whatever else they do - a light's
own dimmer on the buttons page, in full screen too) or *a master* (a share
of what they are doing, like the grand master for just those lights).

**+ Colour row…** (in Edit, or a light's right-click menu) makes a row of
colour buttons for the selected lights or a group in one go: one button
per colour, side by side, all *one at a time* - pressing Blue turns Red off,
pressing it again leaves the light's own colour.

A *Go to page* or *Folder* button can also **turn off what's on** on its own
page and **turn on** some buttons of the page it opens: a *SHOW* button on
the Preshow page ends the preshow looks and starts the show's first look
as it turns the page.  **Edit → ☰ Pages…** lists every page: name it, open
it, move it up or down (its buttons, grid and name go with it, and the
go-to-page buttons, timeline clips and sound triggers follow), empty it,
or add the next one.

Targets are all lights, a group, a type (movers, PARs, washes, bars, beams) or
a fixed set of lights, and of those all, odd / even, left / right, or a
pattern (*2 on, 2 off*, *1 in 3*...). Effect, move and look buttons have an
**Order** (1 → last, last → 1, centre out, ends in: which way a chase or a
wave runs) and, for a movement, a **Pan / Tilt offset** in degrees.
**Change a button on stage:** Edit → the button → *Open in the programmer*
puts its lights and values in the programmer; change them, then press
**Update button** in the programmer (same place, name and look).
**Suggest buttons for this rig** fills a page from what
is patched. Blackout and the grand master still win over a flash. Pressing a
button is never an undo step; setting one up is. Buttons save with the show.

**How they look** (Edit -> a button -> *Look*, with a live preview of the
tile off and on): its colour; its size, 1-4 buttons across and 1-4 down;
its shape (rounded, square, pill, circle); its fill (outline, solid, glow);
text size S-XL; *Name only* (no small line under it); *Blink while on*
(slow, and still when the computer asks for less motion); one of 32 icons,
or the icon alone. A lit button shows a dot as well as its colour.
**Layout** (in Edit) sets the page's grid: 4-12 buttons across (fewer =
bigger), the rows, and the row height, for one page or every page.
Right-click a button in Edit to **Copy look** and **Paste look** onto
another button or the whole page; what the buttons do stays the same.

### Groups

*Where:* Fixtures → Group

A group is a set of lights you pick often (the back truss, the floor PARs).

1. Select the lights (click, Ctrl+click, or drag in the 3D / the list).
2. Press **Group** under the fixture list (or right-click →
   **Make a group of them…**), name it, **Create group**.
3. Its chip selects them again. A group's **⋯** (or a right-click on its chip):
   **Add the selected lights**, **Take the selected lights out**, **Rename…**,
   **Delete group…**. Buttons and faders that use the group follow the change.
4. Each group gets a **fader** next to the masters at the bottom: its lights at
   that share of whatever they are doing. Each fader has its own colour. Click
   the **dot under its name** (or right-click the name) to pick another: twelve
   to choose from, **Any colour…**, or **Its own colour again**. The phone
   remote's **Faders** tab shows the same colours.

### Palettes

*Where:* Programmer → Looks → + Record

A palette is a colour, a position or a beam setting, saved once and used
everywhere: cues and buttons point at it, so changing the palette changes
every cue that uses it.

1. Select lights and set the look in the programmer.
2. Programmer → **Looks** → the row for **Colour**, **Position** or **Beam** →
   **+ Record**, give it a name, **Record**.
3. Tap a palette to put it on the selected lights.
4. To change it everywhere: set the new look, then the palette's **⋯** →
   **Update from the selection**. **Rename palette** and **Delete palette**
   are there too.

### Cues and playbacks

*Where:* Programmer → Record cue…

A cue is a look kept on a playback (a fader); a playback holds a list of cues
played one after another with **GO**.

1. Select lights and set the look (Level, Colour, Position, FX...).
2. **Record cue…** on the programmer bar: pick the **Playback**, then
   **Record new cue** (or **Merge** / **Replace** an existing one). **Fade in**
   and **Delay** set how it arrives; tick **Clear the programmer after
   recording** to start the next look clean.
3. Raise the playback's fader and press **GO** for each next cue; **Release**
   lets it go.
4. To change a cue: open it, change the look, and **Update** on the
   programmer bar. Cues can be dragged to reorder, inserted (**Insert before**)
   and deleted; Ctrl+Z brings any of it back.

### Effects

*Where:* Programmer → FX

Programmer → **FX**: chases, waves, circles and other movement, colour and
dimmer effects on the selected lights.

1. Select the lights, open **FX**, pick an effect and set its size, speed and
   spread (how it runs across the lights - **Left to right**, **Middle out**...).
2. **Run** starts it; it is recorded into cues and buttons like any look.
3. Running effects show as a chip on the programmer bar: **×** on the chip
   or **Stop mine** stops yours (a cue's, a button's or the timeline's keep
   running).

### Timeline

*Where:* Timeline (bottom left)

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

### A light show per song

A song can keep its own show, so the same track looks the same every night:

1. **♪ Audio → Add audio**, then **Find the song's parts**: the desk listens
   to how loud each 8-bar phrase is and puts *Intro, Build, Drop, Breakdown,
   Outro* markers on the timeline (move or rename them if it guessed wrong;
   your own markers named *verse, chorus, bridge…* work too).
2. **Auto show** now builds one section per part, at those very bars: the
   drop hits where the drop is.
3. **Songs → Keep the timeline as a song**. The song keeps the timeline
   (music, markers, tracks) and the cue lists its cue tracks play.

**Songs** lists them: **Play** puts a song's timeline and cues back and
plays it; **Edit** puts it on the timeline to change, then **Update**. Any
number of songs share the same playback: each brings its own cues with it.
Ctrl+Z undoes keeping, deleting and renaming.

**With Pro DJ Link (CDJs / XDJs):** play the track on the tempo-master deck
and press **Tie to deck** next to its song (the desk remembers the track's
rekordbox id - the same USB stick gives the same id every night). Tick
**Songs follow the DJ decks**: when the master deck plays a tied track, its
song starts from where the deck is, runs at the deck's speed (pitch
included), jumps when the DJ jumps, pauses when the deck pauses, and stops
when the next track has no song. The deck plays the music; the desk stays
quiet.

**Changing songs.** The desk follows the deck that is tempo master, so in a
mix the lights change when the master moves to the new deck (most DJs hand
it over during the mix). The new song's look fades in from the one before
over 2 seconds - no cut to black. **Between songs** (the master plays a
track with no song) the Songs window offers:
- **Autopilot** (default): the song's look fades out and the autopilot's
  own looks fade in, picked by the music, until a tied track plays again.
  Give the autopilot a playback of its own with a few looks (cues); the
  Songs window says so if it shares the songs' playback.
- **Hold the last look**: the lights stay as they were.

**Shows for many songs at once:** Songs → **Make shows for songs…**. Pick
the songs and say what they should be like ("dark techno, red and white,
hard drops"). For each song in turn the desk puts it on the timeline, finds
its parts from its music when it has none yet, has the show designed for
this rig and those parts, builds it and keeps it in the song. The AI
designs it (Settings → AI): it is told the rig - each group of lights, what
it can do (colour mixing, pan / tilt, gobos, zoom), where it hangs, the
room's targets (the DJ, the floor, the crowd) - and the song's tempo, length
and parts, and answers with a design, never DMX: a look per group per part
(brightness, colour, aim, effect), strobe and flash hits on the beat or
the bar, master moves. The desk checks it (nothing a light can't do) and
builds the cues and the timeline. With no AI, or *the desk's own designer*
ticked, built-in rules design it the same way. Each song is its own undo
step; *Edit* a song to change its show by hand.

**The designer hears the music.** Finding a song's parts also notes what
each part sounds like - how loud, the drums (a steady kick, half-time, none),
the bass, a mid-range lead (a voice or a melody: the sound alone can't tell
which), how bright, rising or falling away - and the designer follows it: no
kick, no strobes on the beat; a vocal part gets warmer, softer looks with the
movers on the DJ; a rising part climbs and hits in its last bars. Songs made
earlier are listened to when Make shows for songs runs on them.

**Songs from a rekordbox USB stick (no music files).** Songs → **Add songs
from a rekordbox USB…**, pick the DJ's stick (or its PIONEER folder). Every
track rekordbox analysed with *phrase analysis* on is listed; tick the ones
you want. Each becomes a song with rekordbox's parts (Intro, Up → Build,
Down → Breakdown, Chorus → Drop, Outro; or verses, chorus and bridge), its
tempo and its length - only those few bytes of the analysis files are read,
not the music. Then **Make shows for songs** designs their shows, and **Tie
to deck** (play the track on the master CDJ) lines the song up with
rekordbox's beat 1 by itself. Read from the analysis format as Deep Symmetry
documents it; checked against files built to that description, not yet
against a real stick.

**Change part of a show by asking.** Copilot → Design → **Change part of
it**, or **Change…** next to a song: “make the second drop harder”, “the
breakdown in blue and white”, “no strobes in the intro”, “movers on the crowd
in drop 2”. The AI gets the show as it is and changes only what you name; the
desk checks it like a new show. You see the parts that changed before
building; one Ctrl+Z undoes it. With no AI the desk's own rules understand
harder / softer, strobes or no strobes, colours, where the movers aim, and
slower or snappier fades - and say so when they don't understand. A show
made before this version has no design kept with it: make it once more
(Auto show) and it can be changed from then on.

**A laptop DJ** (Serato, Traktor, rekordbox on a laptop) sends no track
over the network, so songs don't start by themselves. Start one with
**Play**, or from a Stream Deck / Companion key with the OSC message
`/haze/song <name>`; run the autopilot for the rest.

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

### Lining up a venue (the real lights match the desk)

*Where:* Set up → Line up with the real lights…

The desk aims a light from where it thinks the light hangs and which way it
faces. In a real venue those are never exact, so line the rig up once per
venue; it's saved with the show.

1. **Build the room roughly.** Settings → Venue → a template (club,
   concert...), then the room size. In **Arrange**, put the trusses at their
   real height and position (measure from the middle of the back wall:
   x across, z towards the audience, metres) and draw the dance floor zone.
2. **Hang each light where it really is.** Drag it onto its truss, or type
   its position; set its **Hang** (hung, standing, on a boom...). A light hung
   the other way round to the rest: **Invert pan / Invert tilt** in
   Programmer → Rig.
3. **Mark the floor.** Two or three spots far apart: the middle of the dance
   floor, its left and right, the DJ booth - the desk lists the room's own
   spots - or tape a cross somewhere and keep it as a room mark.
4. **Line up.** Set up → **Line up with the real lights…** (or a light's own
   menu). One step at a time: the light comes on alone and the desk points it
   at a mark by itself (the two marks furthest apart). Nudge the REAL light
   onto it with the arrows (**Fine** for the last bit) and press **It's on
   the mark ✓**; then the second mark. The desk works it out, says
   **Lined up**, and **Next light ▶** goes on; a tick shows each light done.
   If the arrows move it the wrong way, that link has Invert / Swap; another
   spot is under **Use a different spot**.
5. **Check.** Aim the whole rig at a spot (Position → room map, or a
   palette like Fan out) and look at the floor. If the box said the marks
   disagree by more than a few degrees, that light's height or position in
   the room is off: fix it in Arrange and line it up again.

The **Plot** (Windows → Plot) shows the rig flat with every light's number -
handy to walk the room and tick lights off.

### The room the lights know: teach it by pointing

*Where:* Set up → Teach the room by pointing…

Once a light is lined up, the desk knows exactly where its beam lands on the
floor - so the light itself can show the desk the room. **Set up → Teach the
room by pointing…** (or the command bar, or Arrange → Add):

- **Mark out a zone.** Pick the kind (Dance floor, Bar, VIP...), aim the light
  at each corner with the arrows and press **Add this corner**; three corners
  or more, in order round the edge, then **Make the zone**. A rectangle, a
  square or any shape. Corners outside the room drawn mean the room is bigger
  than the desk thinks: **Make the room fit**.
- **Not here (keep out).** Aim at what a beam must never hit - a wall, a
  mirror, the bar staff, the DJ's eyes - and press **Not here**. Pointed again
  nearby, the area grows. Aimed past a wall, the whole wall is kept out.
  Every light keeps off it, whatever it plays: cues, palettes, effects,
  roams, the programmer. A beam that would cross it stops at its edge.
- **Keep lights in a zone.** Pick lights and zones: those beams stay inside
  (the floor spots on the dance floor, the bar wash on the bar), whatever
  they play.

Keep-out zones show red in the 3D and on the plot; the programmer keeps what
you set (only what goes to the lights is held). **Position → Roam** makes
lights wander inside the zones you picked.

### Follow me speed

Move tab -> Aim: **Follow speed** sets how the beams follow the pointer on
the floor map or the 3D floor. **Instant** goes straight there, as fast as
the lights can move. **Fast**, **Medium** and **Slow** make the beams glide
after it and carry on to where you let go (a dashed ring shows where they
are aimed right now). The desk does the gliding at the full DMX rate, so
the real lights glide smoothly even with the page hidden or closed. Nudging
or re-aiming the lights by hand stops a glide. Remembered on this computer.

**Make a button…** (at the foot of the programmer) shows what goes in -
Brightness, Colour, Position / movement, Beam - each on when the
programmer holds it for those lights.  Leave only *Colour* on for a
colour-only button ("Colour · 4 lights" on the tile): pressed over a
movement button it changes only the colour, and the movement keeps
running.  A colour picked for lights at 0% brings them up to full (lights
you put at 0 yourself stay there, and the desk says so).

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
* **Move tab -> Aim: the room from the DJ's side.** The map shows the DJ
  booth (with an arrow the way the DJ faces) and the middle of the room.
  *Set: DJ booth / Middle / Front*, then tap the map, to put them where
  they really are (*Reset* goes back to what the desk works out from the
  room).  *Map: From the DJ* turns the map so the way the DJ faces is at
  the top and left is the DJ's left (*From the audience*, or *↻ 90°*).
  *Spot (DJ)* moves the beams' spot left / right / away / nearer as the DJ
  sees it, and *● middle* sends them to the middle; the spots list has a
  *Middle* too.  The map redraws when objects, trusses or lights move.  The
  3D's **DJ** view (key 7) looks from the booth the way the DJ faces, so
  dragging lights there works from the DJ's side.
* **Move tab -> A → B.** A move between two angles in each light's own
  degrees (0 is the middle of its travel): *pan −100° → +40°*, tilt from
  and to, or *whole pan* / *whole tilt* for the light's full travel.
  *Bounce* goes there and back, *loop* starts from A again, *once* goes to
  B and stays.  Speed is seconds a trip (or on the beat).  The line under
  it says what will happen, and *Now:* shows where the first light points,
  in degrees, live.  The arc and the wave take any number of degrees, not
  only the chips.  *Lights: wave* spreads the start across the lights for
  a smooth wave (½ wave, 2 waves, or any degrees).  On a multi-head light
  (a Wave 360) *Per head* gives each head its own tilt from → to.
* **Head strip** (programmer, multi-head lights): each head's colour dot,
  *Copy 1 to all*, *Spread a gradient* from head 1 to the last, and on a
  light with a tilt per head a bar of tilt sliders in degrees and a *Wave*
  through the heads.
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

**Quality → Best: drawn frame by frame** makes the film the way a renderer
does: the show plays once while the desk notes every look and where the
camera is (the button on the 3D view says NOTING), then the 3D draws every
picture at full quality - High, every shadow - taking as long as each
takes, and saves the video (MP4 where the computer can make one, else WebM)
with the timeline's music. It's smooth on any computer: a slow one just
takes longer (the progress shows how long is left; Cancel stops it). Full
HD, 4K or small, at 30 or 60 pictures a second (60 shows fast strobes as
they are).

### Workspaces

The top bar's **workspace** button arranges the screen for the job:
*Set up*, *Live*, *Programming*, *Theatre (cues)* (the cue list of the
playback in focus beside the faders: the live cue marked, a tap is GO to
that cue) and *No 3D (buttons + programmer)* (no visualiser at all: the
programmer and the buttons get the room, and the computer does no 3D
work).  The menu's *3D view* and *Cue list beside the faders* turn those on
or off in any workspace.  (Busking and Show from earlier versions stay in
your list as your own workspaces.)
Alt+1 ... Alt+9 switches between them. Each one keeps the screen as you
left it: the fixture list and programmer shown or hidden, which side each
is on (drag a panel by its title to the side you want - both can go on the
same side - or pick it in the workspace menu), or floating over the 3D view
(drag it by its title anywhere over the 3D; drag it near a side to dock it
again), their widths (drag a panel's
inner edge; double-click for the normal width), the faders / buttons dock at
the bottom or the top and its height, the programmer's tab. *Save as a new workspace* makes your own. Workspaces belong to this
computer (each operator's own), not to the show.

**Small screens (1280 x 720):** the dock's height follows the window, the
group faders fold away (the ‹ beside them) and scroll, the fixture list
keeps at least 5 rows, and panels never squeeze below what they can show.
In Live, blackout, the hits, the tempo and the autopilot come first.  In
full screen the buttons have their own Blackout; pages, Edit and Layout
work there too.

**Help → Take the tour** shows round the mode on screen (Set up, Program
or Live); the first time in each mode the desk offers it once.  A greyed
*Record cue* says why underneath.  **Ready?** puts a button on each thing
to fix (Add lights, Show the clashes, Record a cue, Save, Output
settings, Turn on Stay on the floor...) and *Check again*.

### Touch screens

The desk behaves like a control panel, not a web page: a swipe, a pinch or a
stray palm doesn't scroll, zoom or go "back", and nothing gets text-selected.
Every button, fader, pad and knob follows its own finger - hold BLACKOUT with
one finger and tap Flash with another; a second finger landing on a fader
already being moved is ignored. A finger held still on a button is a held
button, never a menu (the mouse's right-click still opens its menu).

Windows' own gestures (a swipe in from an edge for Task View, notifications or
the taskbar; three or four fingers to switch apps or show the desktop) are
locked by the desktop app **while a desk window is full screen** (Windows 10 and 11:
the window's own edge lock, and Windows 11's own switches for the left and
right edges and three/four fingers) - the
buttons page's ⛶ Full screen, or a Show mode screen - and come back as they
were when it leaves full screen (**Desk → Lock every touch gesture while full
screen**, on by default). For a desk that never leaves the show, also use
**Desk → Lock touch swipes on this computer…**: the same locks for the whole
computer all the time (an administrator's OK once; it starts after you sign
out and back in; **Unlock** puts them back).

### Keyboard

*Where:* Help (?) → Keys

| key | does | key | does |
|---|---|---|---|
| <kbd>Space</kbd> | GO on the focused playback (Enter never fires GO) | <kbd>B</kbd> | cue back |
| <kbd>X</kbd> / <kbd>Shift</kbd>+<kbd>X</kbd> | BLACKOUT / lights back | <kbd>C</kbd> | clear the programmer |
| <kbd>A</kbd> / <kbd>Shift</kbd>+<kbd>A</kbd> | select all / none | <kbd>L</kbd> | locate |
| <kbd>0</kbd>–<kbd>9</kbd> | start a command (`1 thru 4 @ 70`, `3 go`); Enter runs it | <kbd>↑</kbd> <kbd>↓</kbd> | intensity ±5 (<kbd>Shift</kbd> ±1) |
| <kbd>R</kbd> | record a cue | <kbd>G</kbd> | group the selection |
| <kbd>O</kbd> / <kbd>Shift</kbd>+<kbd>O</kbd> | update the current cue (merge) / replace it | <kbd>I</kbd> / <kbd>D</kbd> | insert after / delete the current cue |
| <kbd>F</kbd> | frame the selection | <kbd>/</kbd> or <kbd>Ctrl</kbd>+<kbd>K</kbd> | command bar |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd> | undo / redo | <kbd>Ctrl</kbd>+<kbd>S</kbd> | save show |
| <kbd>?</kbd> | help | <kbd>Esc</kbd> | close the top panel |
| <kbd>Shift</kbd>+<kbd>Space</kbd> | play / pause the timeline | <kbd>W</kbd> / <kbd>E</kbd> | Arrange: move / rotate |

Typing in a field never fires the rig.

### Command bar

<kbd>/</kbd> opens it. It searches every action and also takes desk syntax:
`1-4 red`, `1-8 at 60`, `all dimmer 40`, `1-4 pan 90`, `cue 3 go`.
`at` (or `@`) is the level, as on other desks: `1 thru 6 at 70`,
`3 @ full`, `1-4 at out`. `1-4 aim 90 -30` points them (pan, then
tilt); `cue 3 at 50` runs cue 3 at half.

## The copilot

### The AI assistant (with an AI key)

*Where:* Copilot (top bar)

With `LLM_API_KEY` in `.env`, the copilot's Program tab is an assistant
that works the desk itself. It looks at the rig, does things, then
**checks the real lights** (level, colour, which zone each beam lands in)
and fixes what isn't right, in as many rounds as it needs. Ask for a feeling
(*"the drop needs to hit harder"*, *"calmer for the speeches"*, *"this looks
cheap, fix it"*) or ask a question (*"why is head 7 dark?"*). It asks you
when something is unclear, with answers to tap. It remembers your
preferences (Settings → AI → **What the AI has learned…**: keep, forget or pause). **AI sees the 3D
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
    and stretched to fit the song.  It makes no groups of its own: each look
    picks its lights directly, and when one of your groups has exactly those
    lights the timeline and the buttons use your group's name.  Copies made
    by earlier versions (*Auto · …*) can be tidied away in one tap (the
    *Tidy auto copies* chip under Groups).

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

### Talking with the copilot (voice)

*Where:* Copilot → 🗣 Talk

With the online AI (Settings → AI → your key), **🗣 Talk** in the copilot
starts a spoken conversation: you talk, it answers out loud, and you can talk
over it. What you ask it to do goes to the copilot as if typed - the room's
rules, the preview and one Ctrl+Z all hold. Press it again to stop. Without
the online AI, hold 🎙 to speak one request.

### What the AI has learned

*Where:* Settings → AI → What the AI has learned…

Settings → AI → **What the AI has learned…** shows everything it uses:
notes you gave it ("never strobe the VIP"), things it noticed and wants you to
OK (**Keep** or **Forget**), habits from your shows, and requests you undid.
**Pause learning** stops it learning anything new; **Forget all notes…** starts
again (your shows stay).

### DJ requests from a phone

*Where:* Copilot → 📱 DJ requests…

The DJ or the host asks for things from their phone (more energy, a speech,
blackout for the intro) and you decide.

1. Copilot → **📱 DJ requests…** → **Copy the link**, and send it to them.
   On another device use this computer's network address in place of
   localhost.
2. Each request shows as a card on the desk: **Do it** (the copilot does it
   live, within the room's rules) or **No thanks**. Their phone sees the answer.
3. The link can only send requests, never run the desk. **New code** stops an
   old link working.

### Your phone or tablet as a remote

*Where:* Settings → Phone & tablet

Play the show from a phone or tablet: your button pages, **Faders** (the grand
master and a fader per group, in the groups' colours), **Cues** and
**Timeline** (when the show has them), and **Blackout**, the **Master** and
**Release all** along the bottom. The remote can't edit the show.

1. Let phones in: **Desk → Other computers, phones and tablets…** (the desk
   restarts once and shows a pairing code).
2. The **phone button** in the top bar (or Desk → Other computers, phones and
   tablets → **Show the QR code for phones**) shows a big **QR code**. Scan it
   with the phone's camera on the same Wi-Fi and the remote opens, signed in.
   No camera? Type the address shown there and the pairing code.
3. **On the home screen:** on an iPhone or iPad tap Share → **Add to Home
   Screen**; on Android Chrome ⋮ → **Add to Home screen**. It's named after the
   venue (or the show) - type another name in that box if you like. On Android
   it goes full screen at your first touch (⋯ → *Full screen on the first
   touch* turns that off).

**One icon for every venue.** A desk letting phones in also answers to the
name **haze.local** on the network, whatever its address there. The remote,
opened from the QR code, moves itself to that name when the phone can reach
it - add it to the home screen after that, and the same icon opens the desk at
any venue, on any Wi-Fi, with nothing to scan. iPhones and iPads find the name;
some Android phones can't, and the remote says so once - there, scan the QR
code again at a new venue. A Wi-Fi that keeps its devices apart (some hotel and
venue guest networks) hides the name; use your own router or a hotspot. A
second Haze desk on the same network becomes haze-2.local.

On a phone the buttons are big tiles under their headings, three across.
With the phone on its side the tabs and pages go down the left and Blackout,
the Master (standing up) and Release all down the right, so the buttons get
the whole height. Swipe left or right to change page. A tablet
on its side shows each page in your own grid, the same as on the desk.
**CO2, confetti, sparks, flames, fog and lasers** play on a tap, the same as on
the desk: fire and lasers go only while the desk is armed (**ARM FX**).
**Release all** is a hold. **ARM FX** and **KILL FX** are in the ⋯ menu (only
when the show has effects), out of a stray touch's way. ARM FX works as on the
desk: its box with the safety check, then armed until you disarm (one tap).
KILL FX needs a second tap within 3 seconds.

**Screen lock:** tap the **padlock** at the top and the buttons, tabs, pages
and the ⋯ menu stop answering (a wet, cracked or sweaty screen can touch by
itself; so can a phone in a pocket). **Blackout** and the **Master** still
work. Hold the padlock for a second to unlock - a tap only says so. It stays
locked if the page reloads.

The phone and the desk always show the same: a button lit on one is lit on
the other within a tenth of a second or so, whichever you pressed it on.

*If the phone lets you down:* a button you're holding is let go the moment the
phone is locked or you switch to another app. If the phone loses the Wi-Fi
with a finger still down, the desk lets go by itself after 3 seconds. A tap
made with no Wi-Fi is not sent later. The remote doesn't run in the
background: locked, it sends nothing. The screen stays on while the remote is open (after the
first tap). A phone opening the desk's own address gets the remote by itself.
A tablet can still open the whole desk: ⋯ → **Full desk**.
**⋯ → Arrange buttons on this phone** lets a phone lay out each page its own
way: drag a tile or a heading to move it - it lifts under your finger, a
dashed slot shows where it will land and the others slide aside once your
finger rests there (hold it at the top or bottom edge and the page scrolls
along), **⤢** makes a tile normal, wide, big or a whole
row, **hide** takes it off that phone. **Done** keeps it on the phone; the
desk's buttons don't change. **Desk's layout** puts the page back as the desk
has it. A button added on the desk later shows at the end of the page.
⋯ also hides a tab you don't use on that phone (for example **Timeline tab →
hidden**): each phone or tablet keeps its own choice, and **Buttons** always
stays. **New code** (in
Desk → Other computers, phones and tablets) signs every phone out.

## Connecting a rig (Art-Net / sACN)

*Where:* Settings → Output

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
   **Auto** uses. Universe 1 in Haze is Art-Net universe 0, which is
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

### Strobe safety

*Where:* Settings → Output → Strobe safety

Settings → Output → **Strobe safety**: tick *Strobes at most 3 flashes a
second* and nothing the desk sends flashes faster - buttons, the desk's
strobe, and every light's own strobe channel, even a cue that asks for more.
It is the usual guidance for people with photosensitive epilepsy, and many
venues and events require it. The copilot also keeps strobes off the VIP, the
bar, the DJ and the stage unless you ask.

### A backup desk

A second computer can stand by as a spare desk, so a dead laptop doesn't
take the show with it:

1. On the **main** desk: **Desk → Other computers, phones and tablets…**
   and allow them. Note its address (e.g. `192.168.1.20:8787`) and pairing
   code.
2. On the **backup** desk: **Settings → Backup desk**, type the address and
   the code, and **Follow the main desk**.

The backup copies the main desk's show (every change: lights, cues, buttons,
the room…) and what is live (each playback's cue and fader, the master,
blackout, the programmer, the timeline and the buttons that are on) twice a
second. Its own output stays **off** while it follows, and the status bar
says *BACKUP DESK*: two desks sending to the same lights would fight. Cue
actions (OSC to QLab, macros) run on the main desk only.

If the main desk stops answering for the seconds you set (3 by default),
the backup **takes over**: its output goes on and it carries on from the
main desk's last cue, faders, timeline and buttons, and every screen says
so. **Take over now** does it by hand (e.g. when the main desk's output
broke but the computer still answers). If the main desk comes back, the
backup warns that both are sending; it never hands back by itself - stop
one of them. Both desks need to reach the lights (the same network, or a
USB box moved over). The pairing code stays on the backup computer and is
never saved in a show.

### Philips Hue lamps

Smart bulbs can follow the desk, so a party room or a small bar needs no
DMX. **Settings → Philips Hue**:

1. **Find** looks for the bridge (or type its address: the Hue app shows it
   under Settings → My Hue system → the bridge).
2. Press the round button on top of the bridge, then **Pair** within 30
   seconds.
3. **Show the lamps**: next to each Hue lamp, pick the desk light it follows.
   **Blink** makes a lamp blink once, to find it in the room.

Each lamp copies its desk light's colour and brightness (blackout and the
grand master too). Patch a plain colour light for each lamp (e.g. *LED PAR
4ch* on a spare universe) and program it like any other light. Which light
drives which lamp is saved with the show; the pairing stays on this computer
and is never saved in a show. The bridge takes about ten changes a second in
all, so lamps follow looks and slow fades well; chases and strobes are too
fast for them.

## New shows: templates and the demo

The first time the desk opens with nothing patched, it offers **the demo**:
a club night already programmed and playing in the 3D view (nothing leaves
the computer). It also offers a **template**, or an empty show, and a short
**tour** of the desk (later: Help → *Take the tour*).

**Show ▾ → New show…** has the templates: **Club night**, **Wedding**,
**Band / concert**, **Theatre**, **Mobile DJ** (two movers and a Wave
360 on the DJ's truss, uplights round the floor) and **Corporate event**. Each one builds:
- a room of that kind;
- real lights from the library, hung on its trusses;
- groups;
- colour palettes, and position palettes for each zone of the room;
- a page of buttons;
- a first set of cues and the timeline.

It's a normal show, so change anything. A template, or **Empty show**, is
one Ctrl+Z, and that brings back the show that was open.

## Look and help

*Where:* Settings → Screen & 3D

**Settings → Screen & 3D → Look**, per computer:
- **Density:** *Compact* fits more on screen, *Roomy* gives every button
  and row more room; *Standard* sits between. (**Gig mode** above makes
  everything big for touch screens.)
- **Size: Auto** (the standard) fits the desk to the screen. A 1080p
  laptop, or a 4K laptop with Windows scaling at 250%, shows it at 100%. A
  1440p monitor goes to about 133% and a 4K monitor at 100% scaling to 200%,
  so text and buttons stay the same size to the eye, and the 3D stays sharp.
  A small window (under 1280 x 720) shrinks it, down to 80%.
- **Or pick a size:** 90-140%. A bigger size needs a wider screen (the
  desk is laid out for 1280 px); sizes that don't fit are greyed.
- **Accent:** eight colours or your own.
- **Panel brightness**, and **Show dark:** the panels in dim red for a dark
  venue. Eyes stay used to the dark, and the 3D view keeps its true colours.
- **Reduce motion:** nothing slides, fades or pulses.
- **Hover help:** rest the pointer on any control for what it does (on by
  default). **Look back to how it came** undoes all of it.

Which panels show, on which side and how wide is the **workspace** button
in the top bar (each workspace keeps its own; Alt+1..9 switches).

**Arranging lights:** select some, then the fixture list's **⋯**: line them
up in a row across the stage or at the same height, space them evenly left
to right or front to back, or flip them left ↔ right. **Clear the
selection's values** takes only those lights out of the programmer.
**Settings → Venue → Describe the room** builds a room from words ("a 12 x
8 m club, bar on the left, DJ booth on a 40 cm riser"); *What would it
build?* shows what it understood first.

**Wheels:** under the programmer's tabs, a wheel for each attribute of the
open tab that the selected lights have (dimmer, colour, pan / tilt, zoom,
focus, iris, frost, gobo spin). Drag up or down or scroll to turn. **FINE**,
or holding Shift, turns a tenth as far. Arrow keys work too.

**More universes:** Settings → Output → *More universes* sends any universe
to a node of its own (one node per truss), over Art-Net or sACN. With sACN
you can also set the desk's **priority** (0-200). Both are saved with the
show.

### Getting help

*Where:* Help (?)

1. **Ask the copilot.** "How do I line up the lights?", "what is a gobo?" - the
   answer comes from this guide, as numbered steps with the real button names.
   Tap a button name to see it lit up on the screen; tick the steps as you go.
2. **Open it for me.** On an answer, it opens the window it is about.
3. **Show me step by step.** One step at a time over the real screen, with the
   button to press lit up; pressing it moves on. Esc stops.
4. **Search.** Type in the command bar (/): a topic ("strobe safety"), a word
   ("gobo") or a button's name ("line up") finds it.
5. **What's this?** Help (?) → **What's this?** (or Shift+F1), then click
   anything to see what it is; **Explain this screen** explains where you are.
6. **Hints.** When something looks stuck (blackout on, the master at 0, a
   fader down, playing the show while BLIND) a line says so, with a button that
   fixes it.
7. **Undo history.** The arrow beside Undo lists the last changes in words; pick
   one to go back to before it (Redo brings them back).

Questions the guide couldn't answer are kept on this computer (Help (?) →
**Unanswered questions**) so the guide can be filled in where people get stuck.

### Report a bug

*Where:* Help (?) → Report a bug…

Help (**?**) → **Report a bug…**, or right-click a light →
**Report a problem with this light…** (its fixture file, its DMX and a 3D
picture go with it).
Say what's wrong, tick where (DMX, 3D, programmer, effects, cues, a crash) and
whether to attach the whole show; you see every file before anything goes.
Keys and passwords are never included. **Send report** files it directly
(when the desk has the report relay); otherwise **Save report and open
GitHub** saves a zip to drag into the page that opens.

### A newer Haze

*Where:* Settings → Screen & 3D

The installed app checks once a day. When a newer version is out,
*Haze 0.2.0 is out* shows in the top bar: **Download Haze 0.2.0** (the installer for
this computer - install it when there's no show on), **What's new**, or **Not
now**. Nothing installs by itself. Settings → Screen & 3D → *Tell me when a
newer Haze is out* turns the check off.

## OSC out (QLab, Resolume, video servers)

**Settings → MIDI & OSC → OSC out**: the address and port of the other
program (QLab listens on 53000, Resolume on 7000). **Test** sends
`/haze/hello`. Tick *Tell it what the desk does* and it gets
`/haze/go <list> <cue>`, `/haze/master 0-1`, `/haze/blackout 0|1` and
`/haze/button <id>` as they happen. A cue sends its own: Cue list → ⋯ →
**Actions…** → **Send OSC** (e.g. `/go`, or
`/composition/layers/1/clips/2/connect 1`). Saved with the show, since each
venue has its own video computer.

## Video over the network (NDI)

NDI is live video over the network: OBS, Resolume, vMix, a media server
or an NDI camera on the same network sends it. The desktop app can show it
and send the 3D out as NDI. It needs the free **NDI Tools** (ndi.video/tools)
installed on the desk computer; start Haze again after installing them.
Nothing of NDI comes with Haze. (In a web browser there's no NDI.)

* **On an LED screen in the 3D:** Venue → the screen → *Shows* → **Live
  video from the network (NDI)…** and pick the source (the list fills as
  sources come on; or type its name, as the other computer shows it). The
  3D shows it live. The screen keeps the source's name with the venue: on
  another night it shows the same source again when it's on the network.
* **Over the lights:** FX → Paint the rig → **+ NDI source…**, then
  *Front* or *From above*, as for a video. The lights follow the source
  while this screen has it (**▶ Use here** / **■ Stop here**).
* **The 3D out:** Settings → **Video (NDI)** → *Send the 3D view as an NDI
  source*, its name on the network (*Haze 3D*), its size (960 × 540 to
  1920 × 1080) and pictures a second. What the 3D window shows goes out (or
  the 3D in a desk window when that's all that's open); labels and buttons
  don't. A media server, an LED processor or OBS takes it like any other
  NDI source; Settings says how many are watching.

The video comes in at NDI's lower size, plenty for a screen in the 3D and
for the lights. NDI® is a registered trademark of Vizrt NDI AB.

## Hardware controllers

*Where:* Settings → MIDI & OSC

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

### Stream Deck

**Settings → MIDI & OSC → Get the Stream Deck plugin** saves
`com.haze.desk.streamDeckPlugin`. Double-click it and the Stream Deck app
(6.4 or newer) adds a *Haze* group:

| Key / dial | What it does | Its settings |
|---|---|---|
| GO, Back | the next / previous cue | a playback number (empty: the selected one) |
| Blackout | on / off, the key lights red while it is on | - |
| Desk button | one of the buttons, held while the key is held | page and button number (counted left to right, top to bottom) |
| Tap tempo | taps the beat | - |
| Macro | runs a macro | its name |
| Grand master (dial) | turn: the master; press: full / out | % per click |
| Playback fader (dial) | turn: that playback's fader; press: full / out | playback number, % per click |

The plugin talks to **OSC in**, so switch that on first (port 8000). A Stream
Deck on another computer works too: put the desk's address and port in any
key's settings (all keys share them). The dials start at 100% when the
Stream Deck app starts; the first turn sets the desk to the dial's level.

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

### Build a fixture yourself

*Where:* Fixtures → + Add → Build it yourself…

For a light with no library file and no manual to hand: type it in.

1. **Name it.** Its manufacturer and model (Generic is fine), so you can find it later.
2. **Start from a usual layout** (RGB PAR, RGBW, a moving head spot or wash, a strobe,
   fog, confetti) or **Empty**, and pick the kind of fixture.
3. **List its channels in order.** **+ Add a channel** for each, as the light's menu,
   sticker or manual lists them: a name and what it does (dimmer, red, pan, shutter,
   colour wheel, fire...). ▲ ▼ move one, ✕ removes it.
4. **Values (optional).** For a strobe, a shutter, a wheel or an effect, one line per range
   (`0-9 Off`, `10-255 Strobe slow to fast`) so the desk knows which numbers do what.
5. **Another mode?** **+ Add another mode** for the light's other channel counts.
6. **Save to library** - it shows in the Add list, ready to add to the rig. Test it with
   the real light (right-click → Test this light…).

## Fixture library

*Where:* Fixtures → + Add

**Add lights** searches everything at once in the **All** tab, and
also has separate tabs:

* **Installed:** everything already in your library.
* **Libraries:** the [Open Fixture Library](https://open-fixture-library.org)
  (MIT) and the [QLC+](https://www.qlcplus.org) fixture definitions
  (Apache 2.0). Together that's about 2,400 lights, including budget DJ
  fixtures that aren't on GDTF Share (for example the Chauvet Intimidator
  Wave 360). Both ship with Haze, so they're searchable offline at a
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

**Test this light.** A fixture file can be wrong in ways Haze can't see: a
shutter whose open value it never states, or a channel order that doesn't match
the light's mode. If a new light misbehaves, run the 30-second test: right-click
it → **Test this light…** (also on the Level tab's warning, and offered when you
add a model the desk hasn't seen pass the test). Go live first, then:

0. Haze asks whether the light's own display (or DIP switches) shows the mode
   and address the desk expects. Most "some heads don't move / wrong colours"
   is a light set to another mode. **It shows another mode** lists the model's
   modes: pick the one on the display and every light of that model switches to
   it, keeping its number, place, groups and cues. If one has to move to a new
   address because the mode is bigger, Haze says which, so you can set it on
   the light.
1. Haze lights it at full, white and centred, and asks whether the real light
   is on.
2. If it's dark, Haze tries the shutter's likely open values one at a time. Press
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
   DMX mode doesn't match the one you added, and Haze tells you which mode to set.

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

*Where:* Programmer → SFX

Confetti, CO₂, flame, sparks, fog, haze and lasers are **effects**, not
lights, and Haze treats them that way.

**How effects are recognised.** Every fixture Haze stores is sorted into
light, laser or SFX, whatever its source. An effect's channels get their
own jobs: *fire*, *arm*, *fog output*, *laser output*, *pattern* and so on.
The "fire", "armed" and "on" values come from the fixture's own chart and
are never guessed. For example, a MagicFX Psyco2Jet's safety channel is
armed at 100–155, and 156–255 is its **test mode**.

**Beam bars** (like the Laserworld BeamBar 10B MK3, in the Haze library) get
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

**Adding a fixture that isn't in any library.** In **Add lights**, click
**From its manual…**, then paste the DMX chart or drop the manual's PDF.
With an AI key the copilot reads it; without one the offline reader
handles the usual chart layouts, including multi-language manuals. You
check the table, then save it to your library.
