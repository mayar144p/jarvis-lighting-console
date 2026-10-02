---
name: fixture-debug
description: Debug the desk against real fixtures - a brand ("debug Chauvet"), a product ("debug the Antari Z-1000", "the Sharpy"), or the top 20 brands one by one. Use when the operator names a brand, model or fixture type that misbehaves, asks for a "debug session" on lights / effects machines (confetti, CO2, flame, smoke, haze, lasers), or asks whether the 3D matches what a light does.
---

# Fixture debug: a brand or a product, end to end

Checks real library profiles at three levels, finds what's wrong, fixes the
root cause, and proves the fix. The operator is a lighting operator, not a
developer, so report what each finding means for the real light.

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
