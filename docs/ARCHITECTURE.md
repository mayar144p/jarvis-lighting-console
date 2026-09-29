# Architecture

Jarvis is two programs that ship together: a Python engine that owns all
state and the DMX output, and a browser front end that renders it. The
browser holds no state the engine does not have.

## Engine (`app/`)

Standard library only. `app/main.py` starts three things:

1. **`ThreadingHTTPServer`** serving `web/` and the JSON API.
2. **The output thread.** At `DMX_HZ` (40 by default) it asks the engine
   for a frame per universe and hands it to the Art-Net or sACN sender.
   `app/merge.py` builds frames and is pure: it takes the patch, the
   programmer, the running playbacks and FX, and returns bytes. It resolves
   HTP/LTP, applies fades, curves, limits, the grand master and blackout,
   and packs 16-bit channels.
3. **Optional inputs.** DMX input (`dmxin.py`) and MIDI (`midi.py`). Both
   turn into a status entry when unavailable.

### Actions

Everything that changes the desk goes through one entry point:

```
Engine.act(action, **params) -> dict
```

`ACTIONS` in `engine.py` is the registry. `act()` takes care of the things
every caller would otherwise get wrong:

* **Undo.** A snapshot is pushed before any action not in `UNDO_EXCLUDED`
  (reads, selection, status). Undo and redo restore it.
* **Failure rollback.** If an action raises, the state is restored and the
  error is returned. A half-applied action never survives.
* **The patch lock.** Actions in `LOCK_PATCH` are refused while it is on.
* **Revision.** `act_rev` goes up on every state change, but not for
  read-only actions or dry runs. The live stream uses it to decide when to
  send a new snapshot.
* **Autosave.** A trailing-edge write on the writer thread, so a fader drag
  saves once when it stops rather than 60 times.

`Engine.act_batch(calls, label)` runs a list of actions as **one** undo step
and rolls all of them back if any fails. The copilot uses it.

### Fixtures

* `fixtures.py` is the SQLite library. GDTF files are parsed locally into
  modes, channels, roles and ranges.
* `fixlib.py` reads the Open Fixture Library (`.json`) and QLC+ (`.qxf`)
  formats into the same shape as `parse_gdtf`: roles from capabilities or
  presets, 16-bit pairs, pan/tilt travel, shutter open values and wheel
  slots. Both libraries are bundled as zips in `app/fixlib/`, each with a
  search index, and are parsed only on install (source `ofl:<key>` or
  `qlc:<key>`). `refresh_imports` re-reads them, and any cached `.gdtf`
  file, when `PARSER_VERSION` goes up.
* `gdtf_geom.py` extracts the geometry tree and model files for the
  visualiser, cached per definition.
* `fixture_kind.py` classifies a head from its channel roles and name into a
  **physical type** (moving spot, wash, beam, PAR, batten, pixel bar, blinder,
  strobe…) and a **brand**, picks its design role for show generation, and
  places newly added heads on stage in rows by type.

### Venue, quick buttons and the timeline

* `venue.py` is the room as data: size or a traced outline, stage, zones,
  rigging (every rig is a segment a→b), objects, marks, saved cameras, crowd
  settings and a floor-plan underlay (stored by content hash under
  `data/underlays`). Heads carry an optional `mount` ({rig, t}) and
  `stance` (hang/stand); `_reflow_mounts` puts mounted heads back on their
  rig after any venue edit, so a moved truss carries its lights.
* Quick buttons are an **override layer** passed into `merge.resolve_head`:
  a flash raises intensity (HTP floor), kill forces it to zero, a colour bump
  overrides colour, and strobe gates the output in time. Blackout and the
  master still apply after it. Button holds track their owners (a hand, a
  timeline clip) so one never releases the other.
* `timeline.py` is the timeline document. The engine runs a 50 Hz timeline
  thread: point events (cue GOs, one-shot buttons) fire when the playhead
  crosses them, span clips (held buttons, effects) start and stop as the
  playhead enters and leaves them, and level tracks are interpolated each
  tick. A seek chases: last cue re-fired, spans re-held, levels applied.
  The transport is never an undo step; timeline edits are.

### Output target

Every venue's node has its own address, so where the DMX goes is desk
state, not only `.env`. `Engine.dmx_target` is `{mode, host, transport}`:
**auto** (the broadcast of the adapter facing the rig, the Art-Net 2.x
range first, then 10.x, then any private network, using each adapter's real
mask), **node** (unicast to one IP) or **broadcast** (an address you give).
It is saved in show files and autosave but is never an undo step, and the
copilot can't change it. `_get_sender` resolves the target on every frame
from cached values and swaps the sender when it changes, so there's no
restart. `netif.py` lists the adapters (`ipconfig`, `ifconfig` or `ip`) and
checks whether a node is reachable, suggesting an address when it isn't.
Scans poll each adapter's own broadcast as well as 255.255.255.255, which
leaves by the default route only.

### AI

* `llm.py` is a small OpenAI-compatible client. `structured()` gets one JSON
  object back by forcing a tool call against a schema, falling back to
  JSON-in-text for endpoints without tools. It retries once on 429 and 5xx.
* `console_ai.py` builds a compact **rig context** (types, groups, palettes,
  cues, selection), asks for a plan, and validates every step against an
  **allowlist** of actions and parameters. It denies arming, show files,
  imports and deleting heads. Plans are previewed, then applied with
  `act_batch`. A deterministic keyword compiler covers the offline case.
* `showdesign.py` turns a brief into concepts staged on the patched rig.
* `autoshow.py` builds a whole show in three layers. `analyse` makes groups by
  type and location, capabilities, aim targets and song facts. The **design**
  (sections × group looks, hits, master) comes from the model or an offline
  designer, and is validated against the rig. `compile_calls` turns it into
  engine actions (groups, one cue per section, quick buttons, a timeline
  document) applied through `act_batch` as one undo step.
* `doctor.py` inspects the show and returns findings, each with a severity
  and, where possible, an action that fixes it.

## HTTP API

| Route | Method | Purpose |
|---|---|---|
| `/api/status` | GET | public: branding, output, AI availability |
| `/api/fixtures` | GET | public: library search, with the physical type of each |
| `/api/console` | POST | `{action, params}` → `Engine.act` |
| `/api/console/stream` | GET | live server-sent events (below) |
| `/api/console/ai` | POST | copilot: plan, or apply previewed `steps` |
| `/api/console/generate` | POST | show concepts from a brief |
| `/api/console/doctor` | GET | show health findings |
| `/api/console/scan`, `/patch`, `/save`, `/load`, `/import_show`, `/midi`, `/look` | POST | rig and show operations |
| `/api/gdtf/*` | GET/POST | GDTF Share login, search, download, geometry and models |
| `/api/console/underlay`, `/api/console/audio` | POST/GET | floor-plan images and timeline audio, stored by content hash |
| `/api/console/network` | GET | adapters, output target, reachability check |
| `/api/console/autoshow` | POST | design a whole show for the rig, or build a previewed design |
| `/api/fixtures/*` | POST | edit the library: channel labels, ranges, create, import |
| `/api/fixtures/library` | GET | search the bundled OFL + QLC+ libraries |
| `/api/fixtures/library/install` | POST | install one bundled fixture (`src`, `key`) |

**Security.** Everything under `/api/` needs the token except the two
public GETs, so a new endpoint is private by default. A token is required
when the server is not bound to loopback, and it is compared in constant
time from a header only. Every request also passes `_same_origin`: a
matching `Origin` if one is sent, `application/json` on writes, and a
loopback `Host` on a loopback bind to block DNS rebinding.

### Live stream

`/api/console/stream` is one long response of server-sent events. It
replaces polling:

| event | rate | content |
|---|---|---|
| `snapshot` | on change, at most 4/s | full desk state (patch, programmer, playbacks, selection) |
| `lite` | 10 Hz | output counters, faders, now-playing |
| `look` | up to 30 Hz, while anything changes | per-head light: colour, intensity, pan/tilt, beam |

The client reads it with `fetch` (so it can send the token header),
reconnects with backoff, and shows a banner while disconnected.

## Front end (`web/`)

Vanilla ES modules loaded by an import map. No bundler; three.js is vendored.

* `app/store.js` holds the latest snapshot and look and notifies subscribers.
  `app/api.js` does requests and the stream. `app/actions.js` wraps
  `run(action, params)`, the only way the UI changes the desk.
* Panels (`fixtures.js`, `programmer.js`, `playbacks.js`, `topbar.js`,
  `stagepanel.js`, `copilot.js`, `cmdbar.js`, `dialogs.js`) subscribe to
  the store and render.
* `js/stage/` is the visualiser:
  * `models.js` builds a procedural body for each physical type, styled by
    brand (colours, finish, badge texture).
  * `gdtf.js` and `parse3ds.js` swap in real GDTF meshes when the profile
    has them. The 3DS parser validates every chunk, because real files are
    often truncated or padded.
  * `materials.js` has a shared surface shader lit by up to 32 beams (with
    analytic gobos) and additive volumetric beam cones.
  * `venue.js` builds the floor, flown truss with hoists, and performers.
  * `stage.js` is the `Stage` class: rig, looks, selection, camera,
    picking and dragging, bloom. `FixturePreview` renders one model for
    the fixture picker.

A head's model is built as soon as the head exists in the patch, from the
type and brand the engine reports, so it appears the moment it is added.

## Tests

`tools/selftest.py` is one file of isolated suites. It exercises the engine
directly, runs the HTTP server on a free port for route and security tests,
and runs web modules under node (with an import hook that maps three.js to
the vendored copy) to check the picker maths and the 3DS parser.
