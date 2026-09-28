# JARVIS — Console design

> **This file was reconstructed on 2026-09-28 after it was destroyed by accident.**
>
> A throwaway script of mine opened this file for writing — which truncates it to zero the
> moment it is opened — and *then* hit a `UnicodeEncodeError` on a `✕` in the text it was
> writing. The encode error was the visible symptom; the truncation was the damage, and it had
> already happened before the error was raised. There was no git and no backup.
>
> **What is verbatim:** §17.18–§17.23 and everything below this notice, which were written
> after the loss and are exact.
>
> **What is reconstructed:** §1–§17.17. The original prose is gone. What replaces it is an
> index derived from the **test suite that covers each area** — so every claim in it is
> checkable by running that suite, which the original prose was not. The feature set, the
> design decisions and the reasoning behind them are all still true; only the wording is new.
>
> The lesson is in `tools/docpatch.py`, which was written afterwards: never open a document for
> writing until the new text is encoded and known good. Write a sibling temp file, prove it
> reads back, then move it over. A failure then leaves the original untouched.

---

## The shape of the thing

A lighting console in pure Python stdlib plus vanilla JS. No pip installs, no build step,
classic `<script>` tags, no CDN and no external resources of any kind.

The console **is** the desk. There is no external console to drive and no second engine: the
same code that patches, programs and previews also merges, sequences and puts the show on the
wire, because a bridge between two engines is a place for a cue to be lost.

| layer | file | what it owns |
|---|---|---|
| output | `app/merge.py` | the frame. Pure functions, no I/O, no state — which is what makes it benchmarkable |
| output | `app/artnet.py`, `app/sacn.py` | the two transports behind one interface |
| engine | `app/engine.py` | patch, programmer, palettes, presets, cues, playback, FX, undo, the command line, the lock |
| library | `app/fixtures.py` | GDTF parse, the channel→role map, the profile editor |
| library | `app/gdtfshare.py` | the GDTF Share client (login, search, download) |
| client | `web/console.{html,css,js}` | the operator UI |
| view | `web/viz.js` | the 3D view, WebGL with a 2D poster fallback |
| tests | `tools/selftest.py` | 1484 checks, 43 suites, one command, no arguments, ~16 s |

**Where the frame is built, and why it is pure.** `merge.build_frames` takes the patch, the
programmer, the active playbacks, the effects, the master and blackout, and returns a dict of
512-byte arrays. It has no I/O and no state, so it can be measured on its own. Measured
(§17.17 territory, and the reason there is no C++ here): a 40 Hz tick has a 25 ms budget;
`build_frames` is 0.028 ms at 8 heads and 7.4 ms at 2000, and the whole tick including feeds
is 1.8% of budget at 8 heads and 24.6% at 500. The only real argument for C++ is
determinism, and that is answered by a small native output daemon rather than a rewrite.

---

## §1–§17.17 — reconstructed index

Each row names the suite that proves it. `python tools/selftest.py` runs them all; the counts
are per-suite and are checked continuously, so a row that stops being true fails the build.

### Fixtures and the library

| § | area | what it is | suite |
|---|---|---|---|
| 1–6 | the channel→role map | `channel_role` turns a vendor's channel name into one of 29 roles. The whole console is built on this indirection: a fixture is a list of roles, and everything above it is written against roles | `channel roles` |
| 7 | the database | `fixtures.db` with `fixtures`, `modes`, and the mode's channel list | `database`, `fixture profiles` |
| 8 | GDTF parsing | the importer, including 16-bit pairs | `gdtf parser` |
| 9 | the GDTF spec | three real spec bugs found only by testing against live files: the document is `description.xml` at the archive root (not `*.gdtf`); `Manufacturer`/`Name` are **attributes of `<FixtureType>`**; `DMXModes` is a child of `<FixtureType>` | `gdtf spec layout` |
| 10 | the GDTF Share client | login, search, download, with an injectable transport so the flow is testable without the network. Credentials never reach the browser; only the 2-hour session cookie is persisted | `gdtf share` |
| 11 | profiles for brands with no GDTF | `create_profile` from a list of channel names, re-creating to **replace** | `fixture profiles` |

### Output

| § | area | what it is | suite |
|---|---|---|---|
| 12 | Art-Net | packets, and **discovery** — an ArtPoll is a question, and a node's answer tells you its address and its idea of where to send data | `artnet packets`, `artnet discovery` |
| 13 | sACN / E1.31 | the same 512-slot frame through the same sender interface | `sacn transport` |
| 14 | DMX input | a desk driving this one, which is the reverse direction and a genuinely different problem | `dmx input` |
| 15 | 16-bit channels | one logical value across two slots, and the difference between a logical value and a byte | `16-bit dmx`, `16-bit channel sheet` |
| 16 | the network | the destination address, and why broadcast is the wrong default (§17.22) | `network address` |
| 17 | the realtime budget | the 40 Hz tick, measured, with a ceiling that fails the build if it is missed | `realtime budget` |

### The operator loop

| § | area | what it is | suite |
|---|---|---|---|
| 17.9 | undo/redo | snapshot-based, not inverse commands. Playback, cue_go, blackout and master are **excluded** — a fired cue is an event, not an edit. Continuous actions coalesce by refreshing the timestamp and **keeping the original state**, so a drag from 0 to 100 undoes to 0 | `undo / redo` |
| 17.10 | selection tools | `select_similar` (on the *squashed* model, so a renamed profile can never orphan a head), `select_query`, `add`, and a patch filter bar with "select shown" | `selection tools` |
| 17.11 | the DMX channel sheet | per-channel label, control, and the byte on the wire, read from the frames `build_frames` **actually produced** — not re-derived from roles. `raw` and `unused` are surfaced, not hidden | `dmx channel sheet` |
| 17.12 | cue-list editing | insert, delete, move, rename, edit. Every list edit renumbers; the playback pointer follows the **cue**, not the index | `cue-list editing` |
| 17.13 | fanning | 5 modes, over selection **order** by default, `by="position"` optional. Heads lacking the attribute are **reported**; `random` is deterministic per head | `fanning` |
| 17.14 | the attribute grid | values are the value or **MIXED** — never an average. Partial-capability attributes are marked, not hidden. `set_attr_range` reports what was **stored**, not what was requested | `attribute grid` |
| 17.15 | palettes | store **attribute values** `{role: value}`, not per-head rows. The old per-head shape was a preset wearing a palette's name, and it wrote to heads the caller never selected; that bug is now unexpressible. Old shows migrate by majority value per role | `palette targeting` |
| 17.16 | presets | distinct from palettes: a complete look across arbitrary roles | `console engine` |
| 17.17 | the 3D view | WebGL with a 2D poster fallback, a camera system, and beam geometry | `moving-head aim`, `console ux contracts` |

### Rig and show tooling

| § | area | what it is | suite |
|---|---|---|---|
| — | the layout generator | "here are all the lights — make me layouts" | `layout generator` |
| — | the show reader | read a show file back in | `show reader` |
| — | the show builder | seed a show from a rig, with palettes and a cue | `show builder` |
| — | show design | custom shows with a live visual preview | `show design` |
| — | auto patch | re-pack addresses with no overlaps | `auto patch` |
| — | rig discovery / simulated scan | poll the network and patch what answers | `rig discovery`, `simulated scan` |
| — | FX and autosave | running effects, and the autosave that survives a restart | `fx + autosave` |
| — | auto-follow | the chase/follow system | `auto-follow` |
| — | MIDI | in and out | `midi` |

### Interfaces

| § | area | what it is | suite |
|---|---|---|---|
| §10 | the console API | `POST /api/console {action, params}`; errors are **HTTP 200 with `result.ok=false`**, so one client path handles every outcome | `console api` |
| — | the AI console compiler | plain text in, an action plan out | `AI console compiler` |
| — | the agent | the offline tool surface, and the LLM loop | `agent offline`, `agent llm loop`, `console agent tool` |
| — | the client contracts | the DOM and the API have to agree | `client contracts`, `console ux contracts` |

---

# §17.18–§17.23 — verbatim

These were written after the loss, so they are exact.

### 17.18 The channel→role editor — done

A channel whose vendor label is not in the role table carries DMX and responds to the light,
but the desk has no control for it. That state was invisible everywhere until the channel
sheet made it visible, and **visibility without a fix is just a better way to be stuck**. The
add-heads dialog now has a channel editor: every mode, its footprint, and each channel's vendor
label beside the role that label resolves to.

`raw` is amber and `unused` is neutral italic, because they are opposite things: `raw` is a
label we do not understand and may want to fix, `unused` is a channel the fixture genuinely does
nothing with. Painting them alike would nag about the second and train the operator to ignore
the first. Neither is offered as a choice in the dropdown, so "this channel does nothing" is
never one click away.

The editor rewrites the channel's **label** rather than storing a role beside it, because role
is derived from the label everywhere in this codebase. A profile that stored roles separately
would drift the first time anything read it through a different path, and the drift would be
silent.

**`remap_heads` is the load-bearing part.** A head's map is resolved from its profile, and the
profile is editable — so without re-resolving, fixing a channel leaves every already-patched
head un-drivable until a restart, which reads as the fix having done nothing at all. That is
why a fixture editor bolted onto an existing library tends to be abandoned. It reports which
heads changed, which controls were gained, which heads still carry unmapped channels, and it
**drops** programmer values for roles that no longer exist.

Verified end to end: a three-channel profile written by hand patches as `0 controllable`,
channel 1 is given `Color Wheel`, two heads re-map, and `wheel = 200` reaches the wire.

### 17.19 The fixture's own travel, and a channel domain that was half the rig — done

The importer compiled every profile down to a bare list of channel **names**, so a light's
physical travel was thrown away. Pan could only be an abstract 0–255, and the visualiser's
`TILT_SPAN = 270` was a guess from a manual never checked against a real file.

**The range is not where the spec's older drafts put it.** `LogicalChannel/@Min|Max` — the first
thing anyone reaches for — is **absent** from everything the Share serves. The travel is on
`ChannelFunction/@PhysicalFrom|PhysicalTo`, paired with `@DMXFrom`, and one field held the
*opposite* meaning: `LogicalChannel/@Min|Max` is the physical range while `@DMXFrom|DMXTo` is
the DMX end. Reading either into the wrong slot produces a plausible number that means nothing.

The real file, `rev9044.gdtf`:

| channel | travel | notes |
|---|---|---|
| Pan | **−270 … +270** | 540° of pan, not 360 |
| Tilt | **−117 … +117** | **234°, not the 270 the visualiser assumed** |
| PositionMSpeed | 20 … 2 | inverted — DMX 0 is the *fast* end |
| Focus | 10 … 1 | inverted |
| Color1 / Gobo1 / Prism1 | 0 … 1 | a position, not a measurement |

An inverted range is kept in the order the file wrote it, so it falls out of the conversion
arithmetic rather than needing a special case.

**The bug this exposed is the important part.** The console accepted 0–65535 for every channel;
`merge` *clamps* an unpaired channel to 255 on the way to the wire. So the encoder displayed
whatever you typed, the light was sent 255, and **the entire upper half of every 8-bit channel
was unreachable** — an 8-bit pan could not be aimed at 90° at all. The domain is a property of
the channel's **width**: 0–100 for a level (percent-scaled on the way out, whether it lands in
one slot or two), 0–255 in a single slot, 0–65535 across a `role`/`role_fine` pair. Two existing
tests were asserting the bug, and were corrected rather than worked around.

Measured on the fixture actually in the rig: `pan = 90°` puts **byte 170** on an 8-bit channel
(`(90+270)/540×255 = 170.25`) and `43690` on the 16-bit pair. Before the fix both sent 255 —
full pan — while the screen said 90°.

`unit` is **explicit**. With no `unit` a number is logical (0–100 or 0–65535 by width), which is
what every other caller means; `unit="degree"` converts through the fixture's own range.
Guessing is the trap: "90" means 90 of 65535 to a script and 90° to someone looking at a box
labelled °, and silently picking one is how a head ends up pointing at the wall. A wrong unit is
refused *by name* ("pan is measured in degrees, not percent"), and a channel that is not an
angle says what it is ("it is a position value"). Clamping is reported in the unit the operator
typed — "asked for 900°, clamped to 270° — this head travels −270..270°" — never the internal
141992.

**A mixed selection gets no single answer.** Two movers with different travels report both and
refuse the conversion by name, because a made-up midpoint would put 117° of travel on a head
that has 90. A *logical* value still works on that selection, since it means the same thing
everywhere.

**Profiles written by hand can carry travel too** — a line may read `Tilt = -117..117`. Without
it a hand-written mover is addressable but not aimable. The channel editor's **travel** column
edits it per channel, and `..` is the separator because every negative number starts with a
minus.

The look feed now carries `deg: {pan: [...], tilt: [...]}` per emitting head, so the beam is
drawn where the head actually points. The hardcoded 270 is only the fallback for a profile with
no GDTF.

### 17.20 A command line — done

Not a scripting gimmick. On grandMA, MagicQ and Eos it is the **fastest** way to do ordinary
work — "heads 1–4 to 90 degrees" is one gesture instead of a drag, and it is the only practical
way to do the same thing to a *second* group.

**The parser is in the engine, not the client.** One implementation means the console, the HTTP
API and the agent all mean the same thing by `1-4 pan 90`. A second parser in JavaScript would
disagree with the first about exactly the ambiguous cases — and the disagreement would be
invisible until something fired at the wrong heads.

Three properties earn their keep, and all three are things a naive version gets wrong:

**One line is one undo step.** `3-4 dimmer 33` selects two heads *and* sets an attribute — two
actions internally, one Ctrl+Z. The undo button names **the line you typed**
(`undo: 3-4 dimmer 33`), not the action underneath.

**A line is all-or-nothing.** If any step fails the whole line rolls back — **including the
selection**, because a failed line that quietly left the selection moved means the *next* line
acts on heads nobody chose.

**A `cue go` line costs no undo step at all.** Firing a cue is an event, not an edit — the same
reason `cue_go` is excluded from the button path. Charging a Ctrl+Z for pressing GO is how
people stop trusting undo.

`off` **removes** an attribute rather than setting it to zero. A zero is a value that overrides
the palette underneath; a removal lets it show through again.

`+N` / `-N` move **each head from its own value**, so a mixed selection stays uneven in the same
proportions. One "old value" from the first head would level them, the opposite of "these are
10% brighter". A step that cannot be taken in full **names the heads that stopped**, in both
directions.

`cue 3 at 50` is **execute-at**, scaled once at go-time against each head's own channel width —
not per tick, which would put a dict lookup on the 40 Hz path for a feature almost nobody uses,
and scaling everything by a flat 255 would be wrong for a level and for a 16-bit pan at once.

Ambiguity is resolved by judgement and documented: **a bare number before `go` is a cue** — one
head cannot go anywhere. Trailing junk is refused rather than half-run. A typo gets the word that
was meant (`pann` → *did you mean pan?*). A range of unpatched heads is **counted, not listed**.

In the client: `/` focuses the line from anywhere, up/down walk the history, and **Tab
completes only to attributes the current selection actually has**.

### 17.21 Arrange, degree aim, patch sheet, help — done

**ALIGN / DISTRIBUTE / MIRROR.** The thing you do twenty times an hour and cannot do by
dragging. The distinction between the three **is** the feature:

| | what it does | when |
|---|---|---|
| `align` | every head on **one line**, at the mean | the heads are each slightly off-axis |
| `distribute` | **evenly spaced** between the two outermost | a bar of the right length, wrong spacing |
| `mirror` | reflect the shape about a centre | building the left half of a symmetric rig |

A row of six with the middle two bunched is *not* misaligned — every head is off-axis, so there
is no one line to align them to — and aligning it collapses the arrangement to a point.
Treating the two as synonyms is the standard way these get built wrong.

`distribute` measures its gaps in **position order, not head order**. A rig numbered 1…n along
a bar makes those the same list, which is precisely how a head-number-ordered bug survives
every check; a rig numbered by fixture type is where it shows.

`mirror` is **per head, not set-based**, and saying otherwise is a lie about which head is
where: a bar of four at −3, −1, 1, 3 mirrored about 0 produces the same four positions, but
the heads *swap ends*, so it is a real move. The only genuine no-op is a head already on the
line.

**AIM in degrees.** `1-4 aim pan 90` / `1-4 aim 90 -45` (pan then tilt, positionally). With no
`unit`, a bare number is **degrees when every aimable head knows its travel and a logical value
otherwise** — decided from the *fixture*, not from the shape of the number. The **client sends
no `unit` at all**; the server decides, because only it knows the fixture.

`set_position` on a selection with no pan anywhere **reports `aimed: false` rather than
raising** — it is also the "point everything at centre" step over a mixed rig, where a row of
PAR cans legitimately has nowhere to aim.

**PATCH SHEET (CSV).** The document the rig actually has. Until this existed the agent's own
instructions sent operators to *"File > Print Window > Create CSV"* in another program — and a
hand-typed patch sheet is how the **DMX mode column goes missing**, the one column that decides
whether the sheet is usable. The agent's instructions are corrected to point at this.

20 columns, read from the **live patch**, including `Mode`, `End`, stage position to 2 dp, and
`Source` so a profile from the Share says `rev9044.gdtf`. RFC 4180 quoting, a BOM for Excel,
and a filename-safe name. Writing to disk is opt-in; a directory gets the **show's name**.

The client builds the same sheet from the live `lite` feed rather than fetching it — no route
to fail — and the two are checked against each other so they cannot drift. That is a deliberate
exception to "one parser", and the reason it is safe is specific: the command line changes what
the desk *does*, so two parsers would fire the wrong thing; a CSV formatter changes nothing.

**HELP.** `?` anywhere. Built from the DOM and the live state, and the command syntax comes
from the **engine's own help text** — a hand-written second copy drifts, and an operator who
cannot find a feature concludes it does not exist.

### 17.22 The right network address, per-fixture limits, and the lock — done

**`DMX_HOST` now defaults to this machine's own LAN address.** Art-Net is not a protocol you
can address by broadcast alone: a node answers a **subnet-directed** poll from the machine that
holds an IP on that subnet — which is why a laptop moved to a different Wi-Fi finds nothing, and
why a rig that scanned yesterday scans blank after a DHCP lease change.

| | |
|---|---|
| `127.0.0.1` | this machine, and only this machine |
| `255.255.255.255` | broadcast, which many nodes ignore and a switch drops |
| **the LAN address** | what a node answers a poll from |

It is **discovered, not configured**: a UDP socket connected without sending anything, and
`getsockname()` reports the address the kernel would actually source from. A **private address
wins over a public one**, because on a machine with both Wi-Fi and a VPN the public one is
usually the interface the lights are *not* on. Measured: `192.168.0.141` on Wi-Fi 2, with the
VPN's `26.169.25.197` correctly rejected.

`python tools/netinfo.py` answers "is the desk on the right network" and **distinguishes this
machine from a node** — the console's own sender answers its own poll, and without that check
the tool reports *"the network is fine"* on a rig with nothing on it, which is the one answer
that must never be a false positive.

**Per-fixture limits and pan/tilt orientation.** Two facts about one fixture, both of which
look like a broken rig if you cannot write them down: a **dimmer floor** where the lamp is still
faintly lit at "0", and a head hung with **pan and tilt the other way round**.

Both are applied in the **FRAME**, not on write — the only place where "the operator asked for
pan 90" and "the wire needs the other end" can both be true. Clamping in the programmer would
mean the encoder, the channel sheet and the console's own history all disagree after a reload,
and it would only work for the one source that went through it: a cue or an effect would sail
past. Measured: a floor of 8% puts **byte 20** on the wire at value 0, **byte 0** with the limit
cleared, and it beats a playback asking for 0 — because the floor is the truth about the lamp.

**The design/operate lock.** Three states, not two:

| | |
|---|---|
| **DESIGN** | edit anything |
| **OPERATE** | the show runs; the **show** cannot be edited. Faders, cues, the programmer and effects all still work, because that IS the performance. Re-patching a universe is not. |
| **LOCKED** | as OPERATE, and the **patch** is frozen too |

A refusal says **why and what to press**, and costs **no undo step**. The password is a
SHA-256 hash compared length-safely, and leaving a lock does *not* need it — the point is to
stop someone accidentally editing, not the person who set it.

`python tools/featurecheck.py` reports **54 features: 33 yes, 21 no**, and it is written to be
able to say no, including about itself.

### 17.23 Dry run as a button, and the help key — done

**Dry run is a button, beside GO LIVE** — the two questions in order, *may I transmit* and *am I
transmitting*. `dry_run` was startup config, so the only way out of it was to edit `.env` and
restart, which is exactly what stops someone testing on a real rig. It reaches the **existing
sender's flag** rather than rebuilding it: a new sender mid-show drops the first frame and
blinks the rig for a reason nobody can name.

The rule is one-directional. Turning dry run **off while the output is LIVE** starts driving
real fixtures *this tick*, so it asks. Turning it back **on never asks** — that is the safe
direction, and the one you want in a hurry.

**`?` works from a text field**, and does not type itself. It was ignored there, which is the
panel people are in when they go looking for help — the command line — so `?` silently did
nothing from both directions and the key read as broken.

#### Fix: the help overlay could not be closed

Reported as "help does not close", and the screenshot showed it was worse: the dialog was a
**bare heading bar** with an empty body, and none of the ways out worked.

**The cause was an ORDER, not a missing handler.**

```js
if (open) buildHelp();      // <-- throws here...
dlg.hidden = !open;         // <-- ...so this never runs
```

The body was built **before** the dialog was hidden. Anything that threw during the build left
it open with an empty card — and the ✕ that would have closed it runs through the *same
function*, so the way out went with it.

1. **Hide first, build second.** No failure inside the build can leave the dialog open.
2. **The build catches its own errors** and puts a sentence in the body. Blank help is bad;
   help that says it is broken is merely embarrassing.
3. **The dialog handles its own keys** as well as the document's. Four independent routes out
   is not redundancy for its own sake — the one time it matters is when the global handler is
   not reached. A modal you cannot get out of means a page reload, and losing a show because of
   a help page is not a trade worth making.

Verified in the browser with the build **deliberately broken**: 188 px tall with the message in
it, and all four routes close it. Restored, it goes back to 5 sections at 577 px.

---

## Tests

`tools/selftest.py` — **1484 checks across 43 suites, 0 failures**, one command, no arguments,
~14 s. Per-suite exception isolation: a crash is a FAIL and the run continues. `node --check`
on all four web scripts. Performance ceilings fail the build if missed. Three consecutive runs
must be identical.

| | `tools/selftest.py` | 1484 checks, 43 suites (section 12) |
|---|---|

## What is next

`python tools/featurecheck.py` — 33 yes, 21 no, in order of what a show needs:

1. **Colour picker** — an HSV wheel. There is a hex field and swatches; no way to *pick*.
2. **Show management** — browse, rename, duplicate, delete. "Which of these seven files is the
   show?" is a real question at load-in.
3. **OSC** — the only missing protocol with a clear spec; MIDI already exists.
4. **MIDI clock / tap tempo** — the follow system exists and works; a clock on top is small.
5. **SUPER SCENE** — scenes layered on a timeline. The largest gap, and the one I would not
   start without a clear go-ahead: a second timeline engine on the playback stacks, and a
   half-built one is worse than none, because a timeline that looks right and fires at the
   wrong time is how a show stops.

---

### 17.24 A colour picker — done

There was a hex box and eleven swatches. That is not the ability to **choose** a colour; it is the
ability to type one you already know. Typing `#ff8800` is a lookup, not a decision, and the part
that is hard is the part where you do not yet know the answer. So: a **hue ring with a
saturation/value square inside it**.

A wheel, not a square, because a plain hue wheel has no brightness axis and therefore **cannot pick
a dark colour at all** — and a dimmed gel is half of what a lighting desk does. A square alone
cannot show hue, because hue is a circle. The ring-around-a-square is the only 2D arrangement that
shows all three dimensions at once, which is why every picker converges on it.

**The picker counts what it will reach, before you click.** `engine._colour_values` takes the first
family a head has — RGB, then CMY, then white — and returns *nothing* for a head with none of them,
which is a colour wheel. There are two of those in the real rig. So picking with one selected
raises `selected heads have no colour channels`: correct, and useless, because the operator did
nothing wrong and the console still will not say *which* heads. The count is therefore computed
client-side from the same precedence, and shown **while** you pick:

| selection | what it says |
|---|---|
| nothing | `select heads to pick a colour` |
| 5 × RGBW PAR | `reaches 5 heads` |
| #18 + two PARs | `reaches 3 heads` |
| #18 + #17 (a spot) | `reaches 1 of 2 heads · head 17 has no colour channels` |
| #17 only | `no colour channels — head 17 has a colour wheel, not RGB`, and the control dims |

Dimmed, never `disabled`: a disabled control is skipped by the keyboard and reads as broken, and
the operator still needs to *see* the colour they are choosing even when this selection cannot take
it. The predicate is "any of red/green/blue", not all three, because that is what the engine tests —
a red-only head counts as reachable, because it is.

**Three controls for one value is two too many unless they all hear about each other.** The picker
moves the hex box, the hex box moves the picker, a swatch moves both, and the **engine** moves the
picker when a colour arrives from a cue, a palette, the agent or the undo button. A control that
disagrees with the show is worse than no control, because it is trusted.

#### Four bugs, and the lesson in each

Every one of these passed every other check at the time it was found.

**1. It was wired but never drawn.** `wirePicker` attached listeners; `renderProgrammer` only
repaints when the hex field holds something parseable, and on a fresh load with a clear programmer
it is empty. The picker was a blank 196 px square with 51 green checks. *Wiring a control is not
drawing it.* Only counting the canvas's lit pixels found it — 0 of 38 416.

**2. The keyboard moved the wrong axis, and turned a white head black.** The up/down branches
passed the value into `setPick`'s **saturation** slot and left brightness `undefined` → `NaN`.
`NaN` through `hsvToRgb` reaches `rgbToHex`, where `clampInt(NaN)` returns its default of 0 — so a
`NaN` does not look like a `NaN` on the wire, it looks like a **concrete wrong colour**, and
`#000000` was sent to the engine. The contract checks grepped for `case "ArrowDown"`, found it, and
called the key handled.

*Grepping for a switch proves the key is handled; it cannot prove the key moves the axis you
meant.* So the decision became a pure function of `(key, shift, current)` and the suite now **runs**
all ten combinations, asserting that the two axes you did not press come back **bit identical**.
`setPick` also refuses a non-finite pick outright rather than letting a guard downstream turn it
into a colour.

**3. A desaturated colour has no hue, so the engine round trip threw the hue away.** `rgbToHsv`
on `#808080` is hue 0, s 0. A cue or an undo brought a grey in, the feed read it back at 20 Hz, and
the dot jumped to the top of the wheel — after which nudging hue did nothing at all, because the
next tick put it straight back. Keeping the operator's hue is **free, not a fudge**: at s = 0 the
hue is multiplied out and every hue gives the identical colour, which the suite *measures* across
the wheel rather than asserting in a comment. Above one 8-bit step of saturation the guard stands
down, and a real hue is adopted from the engine.

**4. A stale feed tick clobbered the pending pick.** The pick goes out through a 45 ms throttle, so
for up to three feed ticks the engine still holds the *previous* colour — and `renderProgrammer`
wrote that into the hex box unconditionally, so the colour the operator had just chosen snapped
back. Nudging brightness appeared to do nothing. The engine may only write the box when it holds a
**different** colour than the picker is showing.

#### What is measured, not asserted

The maths is four pure functions extracted from the **shipped** `console.js` and **executed under
node**, the same trick `m4Invert` uses, because a transposed red and green yields a colour that
looks like a colour. The cases are chosen to catch that specific swap: **cyan (180°) and magenta
(300°) are exchanged by it and nothing else is disturbed.**

The strongest claim is not a tolerance at all. A hue tolerance is meaningless: at s = 0.25 the whole
channel spread is 19 levels, so a half-level rounding is worth ~1.5° of hue and nothing is wrong.
The invariant that actually matters is **idempotence** — a colour read back out of 8 bits and
re-rendered must be *bit identical*, or the dot creeps under the cursor and a colour cannot be
dialled in twice. That is exact, and it is what the suite asserts. Hue is only asserted where 8 bits
can carry it (s ≥ 0.75), and the geometry is measured too: the square's corners must clear the ring
band, because the two are hit-tested in different branches and an overlap makes pure hue and pure
white unreachable depending on which test runs first.

End to end, through `build_frames` — the same call the 40 Hz thread makes, because a passing HTTP
call proves the API worked and not the output:

| picked | bytes at the head's address |
|---|---|
| ring at 0° `#ff0000` | `255 255 0 0` |
| ring at 120° `#00ff00` | `255 0 255 0` |
| ring at 240° `#0000ff` | `255 0 0 255` |
| an amber `#ff8800` | `255 255 136 0` |
| three digits `#f80` | `255 255 136 0` |
| black `#000000` | `255 0 0 0` |

A drag is ~6 `set_colour` calls, and it costs **one** Ctrl+Z — `set_colour` was already in
`UNDO_COALESCE`. Because coalescing keeps the *original* state, that one undo clears the colour
entirely rather than landing on the drag's first frame, and the intensity underneath it is still
its own separate step. A pick that lands on the colour already shown is not a change and is not
sent at all.

**One divergence the tests found in existing code:** `engine._parse_hex` expands `#f80` to
`#ff8800`; the client's `hexToRgb` rejected it. A picker that rejects a colour the console accepts
is the kind of small wrongness that makes an operator stop trusting a control. Fixed, and the
engine's behaviour is now asserted so it cannot drift back.

### 17.25 The feature survey was under-reporting — done

`python tools/featurecheck.py` is supposed to be measured, and its own docstring says so. But the
behavioural claims were hand-maintained `no(...)` lines, and they had drifted: it reported **no
software lock** and **no invert/swap pan & tilt** — both of which shipped in §17.22. A survey that
**under**-reports is worse than no survey, because the gap it names is a gap somebody will then go
and build.

So the claims about behaviour now **call the engine and read the result** (`probed`), and a client
feature is a source check over the shipped `console.html/js/css` (`client`) that says so in the
`how` column. Two sequencing traps had to be closed for the probes to be honest at all:

- Probing `set_lock` leaves the desk **LOCKED**, and LOCKED freezes the patch — so the next probe
  got `nothing selected` and reported two existing features as missing. A probe must not inherit the
  mode a previous probe left behind, or the survey reports its own test sequence.
- The probe engine is built on the fixture **library** database, which holds profiles and not the
  show, so it starts with an **empty patch** and every head-dependent probe would have said
  `nothing selected`.

**33 yes, 21 no, of 54** — three of those "yes" were features that already existed and had been
missed; the "no" for the show audit came back when the assistant page went (§17.26).

#### The real cause of "the help popup will not close" — and why the first fix did not work

Reported twice. The first fix made the close path bulletproof and the bug survived it, so the fix
was wrong in a way that is worth recording precisely.

**The cause is CSS, not JavaScript.**

```css
.modal { display: flex; }        /* author CSS  */
```

The HTML `hidden` attribute works through the **user-agent** stylesheet's `[hidden] { display: none }`.
Author CSS beats the user-agent stylesheet. So the very next rule in the file silently defeated the
attribute on `#help-dialog`, and `dlg.hidden = true` had **no visual effect whatsoever**.

Measured, after a hard reload with no cache:

| | `hidden` property | computed `display` | on screen |
|---|---|---|---|
| at page load | `true` | **`flex`** | **yes, 1246 px** |
| after clicking ✕ | `true` | **`flex`** | **yes** |
| after Escape | `true` | **`flex`** | **yes** |

**The dialog was never opened. It was permanently open from page load**, with an empty body — which
is precisely the screenshot: a thin bar with a heading and nothing to read. `buildHelp` only runs
when the dialog is *opened*, and it never was.

And no close route could work, for a reason that made it look like a handler bug: every route set
`hidden = true`, which **was already `true`**. The flag said closed and the screen said open, and
both were correct about themselves. There was nothing for the close path to do.

**Why the first fix passed its own tests.** All four close routes were verified by reading
`dlg.hidden` and asserting it became `true`. It did — it was already `true`. *A test that reads the
flag proves the flag is set, and cannot tell whether the element left the screen.* The order fix
(hide before build), the error catch and the fourth close route were all real improvements to real
weaknesses. None of them touched the reason the dialog was stuck.

The fix is one rule, next to the note that already documented the sibling convention:

```css
[hidden] { display: none !important; }
```

`!important` is not decoration. `.modal` and `[hidden]` have the **same** specificity — one class
and one attribute, both (0,1,0) — so without it the winner would be decided by source order, and
the rule can only be written before `.modal`. A convention that depends on nobody ever adding a
`display` is one line away from breaking again, and this one had already broken once.

**The general check, so the next one is found by the suite.** `_hidden_conflicts` scans the shipped
HTML for elements using the `hidden` **attribute** and the stylesheets for an author `display` that
would defeat it, and the suite asserts the answer is exactly the one known offender:

```
#help-dialog: `.modal` sets display:flex
```

Three elements use the attribute; only this one has a class with a `display`. The scan had to be
built carefully, because the first version reported **28** conflicts and was wrong about nearly all
of them: it read this file's own CSS **comments** as selectors, counted `.modal-card` and
`.modal-head` (the dialog's *children*), and flagged every element using the sibling `hidden`
**class** — which is already covered by the global `.hidden { display: none !important }` in
`style.css`. A scan that cries wolf over its own documentation is worse than no scan, because it
trains you to skip it. Comments are stripped, only compound selectors matching the element itself
count, and the expected list is a literal, so a new offender shows up as a diff.

**Verified by measuring the effect, not the flag**, on a hard reload: closed at load; `?` opens it
with 5 sections; ✕, Escape, the backdrop and `?`-again all remove it; `?` works from the command
line without typing a question mark into it; 90 open/close cycles at 40 ms, 120 ms and 400 ms with
zero failures. One failure in an earlier 40-cycle run did not reproduce and is recorded here as a
timing artefact rather than quietly dropped.

---

### 17.26 The assistant page is gone — the app is the console

There were **two front ends for one desk**: `/` was a chat page ("Talk to
Jarvis") with a sidebar, and it linked across to `/console.html`, which was the
console. Every feature had to be built twice or deliberately left on one of
them, and the operator had to work out which one they were in.

`/` **is** the console now. Not a redirect — a redirect means the address bar
says one thing and the desk is the other, and an operator who bookmarked the
wrong one has to notice. `/index.html` serves the console too, so an old
bookmark lands somewhere useful instead of a 404.

#### The AI stayed. That is the whole point of the change.

The AI is not a second product wearing a costume; it was always *inside* the
console, and this is what "console with AI capabilities" now means concretely:

| | |
|---|---|
| `app/console_ai.py` | plain text → validated steps → the same engine, on an **allowlist** |
| `#ai-panel` | the slide-over: compiler, chips, offline toggle, show generator |
| `/api/console/ai` | the compiler, with `offline=` falling back to a deterministic keyword compiler |
| `/api/console/generate` | show-from-a-prompt: a brief in, 2–3 concepts out |
| `app/llm.py`, `app/showdesign.py` | **kept** — the console's AI imports both |

That last row is the one worth being careful about. `llm` and `showdesign` look
like assistant machinery and are not: `console_ai.py` does
`from . import llm, showdesign`, so deleting either would have broken the
console's AI with a bare `ImportError` and no obvious culprit. They were
rescued on the way out, and there is now a check that says so.

The allowlist is unchanged and still the load-bearing part: the model cannot
arm the output, save or load a show file, import, bulk-replace the patch, or
delete anything. Those stay on the operator's own buttons — the ask-first rule.

#### What went, and why it went

Five features existed **only** on that page. All five are gone, and the honest
reason is not tidiness:

| | what it was | why it could not simply move |
|---|---|---|
| free chat | an LLM conversation | a conversation is not a console action; it is the second front end again |
| show audit | "read my show, what would you improve?" | reached the engine through `agent.audit_patches`, outside the console's undo and lock |
| show builder | "create my show from these lights" | `showbuild.program` wrote a patch straight through — a colour the desk applied could not be undone **from the desk that applied it** |
| rig studio | "here are all the lights, make me layouts" | `editor.js` + `/api/layout`, and it was the only producer of a layout session |
| voice / photo / session memory | `app.js`, `/api/session*` | no console equivalent, and speech is a phone, not a desk |

The show builder is the clearest one. It is the same reason the AI is on an
allowlist: **anything that can change the show must go through the console**, or
the console's undo, lock and dry-run story is a lie.

`patch_from_layout` went too, and it is the kind of thing that is easy to leave
behind. It read a layout session — which only the rig studio could produce — so
after the studio it was a door to nothing that would have answered *"no layout
generated yet"* forever. A dead branch that looks like a feature is worse than a
missing one. It is gone from the action table, from the lock's patch set, from
the AI's denylist and from the patch route that defaulted to it.

`style.css` and `viz.js` **survived**, and they are the trap here: both pages
loaded them, which makes them look like part of the page that went. `style.css`
carries the design system *and* the global `.hidden { display: none !important }`
that the help dialog's close depends on (§17.23's fix). Deleting it would have
broken the console's own modals.

#### The stage is now the rig — strictly better than what it replaced

`showdesign.design()` read a **layout session** to decide what to draw its
concepts on. That session came only from the rig studio, so once the studio
went there would have been no session, and show-from-a-prompt would have laid
its concepts out on a **synthetic ten-head fake** while a real patched rig sat
ignored.

So it reads the patch instead. The console always has the real thing: the
operator's own positions, roles and head names. Live on the 8-head rig:

> *concepts are drawn on the 8 head(s) you have patched, at their real positions*

and the assumption line says so, so the operator knows the preview is theirs.
The synthetic fallback now runs only for a desk with an **empty** patch, which
is the one case where there is nothing better to draw on. Both are tested, and
both say which one they are.

#### Found while removing it: the console's AI panel had been unreachable

Not a consequence of the removal — a pre-existing fault in `console.html` that
this work walked straight into.

**`#add-dialog` was never closed.** The HTML parser therefore nested
`#ch-dialog` *and* `#ai-panel` **inside** it, and `#add-dialog` carries the
`hidden` class, so `display: none` hid the console's entire AI. The `AI` button
toggled a class on a panel inside a closed subtree, so it did nothing an
operator could see. Show-from-a-prompt would generate three concepts and put
them in a `<select>` with **zero height**. The DMX channel sheet could not be
opened either — same dead subtree.

One missing `</div>`, and no error, no failing test, and nothing on screen to
explain it. The only thing that found it was measuring `getBoundingClientRect()`
instead of asking whether the class had toggled.

`_html_structure()` now walks the tags and records the **ancestor chain** of
every element with an id, then requires a top-level overlay to have no hidden
modal in its ancestry. "Depth 0" was the first attempt and it was the wrong
shape — the page has legitimate wrapper elements, so depth 1 is fine and depth 7
is the bug. Verified by putting the missing tag back: **three checks fail and
name `#ai-panel` and `#ch-dialog` as being inside `#add-dialog`**. Note what the
balance check does *not* prove — a missing close in the *middle* is recovered
from by construction, so it is the ancestor check that does the work. Both are
kept because a balance error and a nesting error are different faults.

#### Tests

Six suites went with the deleted modules (`layout generator`, `show reader`,
`show builder`, `agent offline`, `agent llm loop`, `console agent tool`): 48 →
43. `show design` **stayed and was rewritten** — `showdesign` is the console's
show-from-a-prompt, so the suite that holds it up is now the thing that has to
earn its place. Its `showbuild` half was retargeted at `import_show`, which is
the engine action the console's own button actually posts; testing the path the
console does *not* use was the wrong half to keep.

A new `console only` suite (43 checks) holds the line: the page and its modules
are gone, the six routes are gone, nothing imports them, `patch_from_layout` is
gone from all four places it lived, the AI is still there and still allowlisted,
`llm`/`showdesign` are still there, the shared assets are still there, and the
top-level overlays are not nested inside a hidden modal.
