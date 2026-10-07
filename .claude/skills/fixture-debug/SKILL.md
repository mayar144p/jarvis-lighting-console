---
name: fixture-debug
description: Debug the desk against real fixtures - a brand ("debug Chauvet"), a product ("debug the Antari Z-1000", "the Sharpy"), or the top 20 brands one by one. Checks the DMX, the safety rules for effects and lasers, whether the programmer offers exactly what the light can do (pan / tilt, how many colours, wheel or mixing), and whether the 3D model matches the product. Use when the operator names a brand, model or fixture type that misbehaves, asks for a "debug session" on lights / effects machines (confetti, CO2, flame, smoke, haze, lasers), or asks whether the 3D or the programmer matches a light.
---

# Fixture debug: a brand or a product, end to end

Checks real library profiles at three levels, finds what's wrong, fixes the
root cause, and proves the fix. The operator is a lighting operator, not a
developer, so report what each finding means for the real light.

## From a bug report ("fix issue #N")

Reports land in the repo's Issues (private) through the forms in
`.github/ISSUE_TEMPLATE/` ("Problem with a light", "Something else is
wrong").  Read the issue with the GitHub tools: the light (brand, model,
mode), where it goes wrong, the steps, and any attachments (screenshots,
its DMX channels, the show file).  Pick the rig from it - `--product` with
the model, or load the attached show on scratch data - then follow the
steps below.  When it's fixed: a selftest for it, a commit that says
"Fixes #N", and one short reply on the issue in plain words.

## 0. Pick the rig

```
python tools/brands.py --brand Antari          # one brand (top 20 or any other)
python tools/brands.py --product "sharpy"      # every library product matching the words
python tools/brands.py                         # the top 20 brands, 15 products each
```

The pick takes up to 15 products per brand: effects machines first
(confetti, CO2, flame, smoke, haze), then lights in turn by kind (moving
head, wash / PAR, bar, strobe, laser, scanner, effect light). It dedupes the
same model across the libraries (Jarvis first, then QLC+, then OFL). If
nothing matches, the light isn't in the bundled libraries: ask for its file
or manual (the desk imports both).

## 1. Engine check (fast, no browser)

```
python tools/brands.py --brand Antari --check
```

Per product, it runs libsweep's checks (Full, Blackout, colour, gobo, move,
Locate, effects, SFX output: DMX vs the 3D look), plus the safety rules:

- **armed-only**: a fire or laser press while DISARMED leaves the wire unchanged.
- **programmed-disarmed**: with every effect / laser channel programmed at
  several values while disarmed, a laser's output channel stays at its off,
  a laser with no output channel keeps its "Laser off / No beam / Blackout /
  Blanking" channel in that range, and the 3D shows nothing firing.

For every mode of a model, use `python tools/libsweep.py --all-modes`.

Does the 3D show every range of every channel (prism facets and turning,
gobo shake / turn / scroll, split colours, a turning colour wheel, pulse /
ramp / random strobes)?  `python tools/lookcheck.py [name]` steps each
channel of the test rig's models through every range its file lists:
MISSED is a bug in `app/beamlook.py` (or in how the file was read);
"not drawn" lists what the 3D has nothing for yet.

## 2. Screen check (the real page, 3D included)

```
node tools/vischeck.mjs --brand Antari  <outDir>
node tools/vischeck.mjs --product "sharpy" <outDir>
node tools/vischeck.mjs --brands <outDir>      # all 20, about an hour
```

Per brand, it starts from a fresh patch, reloads the page, then clicks
through: everything dark at the start, All -> Full, red, one light blue
(the others don't change), movers to the dance floor, rainbow, Blackout and
back, record / clear / GO, then Ctrl+Z. After each step it compares the
feed, the 3D target and the drawn model (lens colour, beam on, pan / tilt).
Then the effects: disarmed, nothing fires; armed, fire / fog / laser each
show in the stream and in the drawing (laser fan, particles); KILL FX stops
everything. Screenshots and `report.md` go to `<outDir>`.

- **Never run two browser checks at once.** The page never settles and
  every click times out.
- A brand of effects machines only (Laserworld, Antari, MagicFX) skips the
  light steps.

## 2b. Fit check: does the desk offer what the product can do, and only that?

`vischeck.mjs --brand / --product / --brands` starts each brand with step
**0. programmer and 3D fit**. It selects each product alone, opens every tab
it shows, and compares that with the light's channels:

- **Tabs.** Move only if it pans or tilts. Colour only if it has colour.
  Laser / SFX only for lasers / effects machines.
- **Colour.** The picker, hue swatches and white temperature only appear
  for a light that mixes ANY colour (all of R+G+B or C+M+Y). A light that
  can't mix gets one button per colour it makes (each emitter, red + green
  = yellow...) and a slider per colour channel. A wheel light gets exactly
  its wheel's colours. The line under the picker never says "mixes any
  colour" for a light that can't.
- **Move.** A pan-only or tilt-only light gets a pad with only that axis.
- **3D body.** It must be the kind of product the library says: moving
  head, scanner (mirror), flower / effect light, bar, strobe / blinder,
  laser, fog / haze.
- **3D cells.** A light with several colour cells (pixel bar, multi-head)
  is coloured cell by cell in the 3D.

A pixel bar's default mode is often one cell. For its pixel modes use
`node tools/pixcheck.mjs <src> <key> <mode>`: it gives every cell its own
colour on the wire and confirms the 3D draws each one.

### The 3D model must match the product

After the fit check, look at each product's 3D model, not only its
numbers. Take a screenshot with the light selected and **Frame** pressed,
then compare it with the product (its name, library type, channels, and
a photo or manual if the operator has one):

- **Kind and shape.** Head on a yoke, mirror scanner, dome, bar, panel,
  strobe box, blinder, laser, fog / haze machine, confetti / CO2 / flame.
- **How it moves.** Pan and tilt, tilt only (a tilting bar), or fixed.
  Every moving channel moves the model, and nothing else does.
- **Count.** Number of heads, cells, pixels, bulbs and beams, as the
  light really has (a 2-cell blinder has 2 bulbs, a 12-pixel bar 12).
- **Colour.** Each cell glows its own colour from the wire, and only
  the colours the light can make.
- **Beam.** It's on exactly when the light is lit, and its width follows
  zoom. A laser draws a fan only when armed and fired.

If any of it is wrong, redo the model:

- If the right model exists, pick it in `app/fixture_kind.py` (name,
  channels, the library type via `lib_type`).
- If it's close, fix the builder in `web/js/stage/models.js`, for
  example cell count, per-cell colour via `pixelMesh` / `sk.pixels`,
  bulbs, or emitters with `cell`.
- If nothing fits, build a new model there and add it to `BUILDERS`.

Then re-run the fit check and pixcheck, and screenshot it again. Only
when a model is a large job (a combo bar with a laser) does it go to
`docs/BACKLOG.md` A8 with what's missing.

When the fit is wrong, fix the desk, not the test. Use ui-ux-pro-max /
design-for-ai for the programmer (show only what the light can do) and for
the 3D models.

## 3. When something fails

Use agent-skills `debugging-and-error-recovery` and ponytail for the
smallest root-cause fix:

1. Reproduce on one product with a short engine script: `fixtures.store_parsed`,
   `add_heads`, then `build_frames()` and `_looks()`. Print the channel map
   (`e._head(n)["map"]`) and the library file's channel ranges
   (`fixlib.load(src, key)[0]["modes"][i]["detail"]`).
2. Decide whether the desk or the test is wrong. Read the fixture's own
   ranges, the way the real light will. Examples from past sessions:
   - real: lasers switched by a colour channel lit while disarmed;
   - real: a fazer's "Volume control" filed as a mode;
   - test: fog runs without ARM by design, and the HTTP look feed is
     lit-only while the 3D reads the live stream.
3. Before changing how channels are classified (`app/fixlib.py`
   `apply_fx`), snapshot every affected library profile's roles, then diff
   after the change. Only the intended products may change.
4. Add a selftest in `tools/selftests/part09.py` using the real library
   file, and register it in `tools/selftest.py`. Show it fails on the old
   desk: run it in a `git worktree` of `origin/main`.
5. Re-run the check that failed, then the gate: `python tools/selftest.py`
   (plus `gigcheck.py` and `oddcheck.py` if the engine changed).

## 4. Rules

- Before changing how channels or bodies are classified, snapshot every
  library product and diff afterwards (the fog / effects / 3D-body diffs
  in past sessions caught side effects before they shipped).
- Safety first. Fire and lasers move only from their armed buttons:
  programmer, cues, looks and the AI never reach them. Blackout and KILL FX
  stop them. Fog and haze need no ARM.
- Tests run on scratch data (`CONSOLE_DATA_DIR`), never `data/`. Never
  print or commit `.env`.
- The repo checks files out CRLF. After `git stash`, strip `\r` before
  exact-text edits.
- Merge flow: push the session branch, open a PR, merge (merge commit) when
  CI is green, then bring the branch to `origin/main`.
- Report in plain words: which products, what the real light would have
  done, what the desk does now.
