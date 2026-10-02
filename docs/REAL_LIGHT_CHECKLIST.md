# Real-light checklist (about 15 minutes)

For a first session with your own Art-Net node and lights.  The tests have
proved the desk sends the right numbers; this proves the **lights** do the
right thing with them.  Work down the list, tick each line, and write a
note (or take a phone photo / screenshot) wherever a light does something
different from "You should see".  Send the notes back and each one becomes
a fix.

**Bring:** the laptop, the node, a network cable, 2 to 6 of your lights
(ideally one LED PAR, one moving head, and the laser / fog if you use
them), DMX cables, a terminator.

**Safety first:** lasers and fog only with the room clear of people's eyes
and smoke alarms covered or isolated.  BLACKOUT (bottom right, or the
**X** key) stops every light; **KILL FX** (top bar) stops every laser and
effect at once.

---

## 1. Connect (2 min)

- [ ] Plug the laptop straight into the node (or into the same switch).
- [ ] Start the desk (`run.bat` / `run.sh`).  The top bar says **BLIND**
      (amber): nothing goes out yet.
- [ ] **Settings → Output** → **Find nodes**.  Your node is listed.  Press
      **Use this node**.  (Some nodes don't answer: type its IP and choose
      **One node: send to its IP**.)
      *Not listed?* Note the node's IP address and the laptop's (Settings →
      Output shows "This computer's networks").  Art-Net nodes usually
      live on 2.x.x.x or 10.x.x.x; the laptop must be on the same range.
- [ ] Patch your lights at the addresses set on the fixtures (Fixtures →
      **+ Add**, pick the model **and the mode the light is set to**, type the
      start address).  Fixtures **⋯ → DMX map**: no red clashes.
- [ ] Press **Go live…** and confirm.  The top bar turns red: **LIVE**.

**You should see:** nothing changes on the lights yet (no levels set).
If a light jumps on, note which and what it did.

## 2. Every light answers (3 min)

For each light in turn, click it in the fixture list:

- [ ] Right-click → **Test this light…** and follow the questions.  Answer
      honestly, "no" is useful.
- [ ] Programmer **Level** tab → **Full**.  **You should see:** that light,
      and only that light, at full.
- [ ] **Out**.  It goes dark.

Note any light that is the wrong one (address), stays dark (mode or
shutter), or flickers.

## 3. Colours (2 min)

Select all colour lights → **Level → Full**, then the **Colour** tab:

- [ ] Red, green, blue, white swatches.  **You should see:** each light
      shows that colour; white looks white (not pink or blue).
- [ ] A colour between two (orange, pink, cyan).  Lights with a colour
      **wheel** jump to their nearest slot; that's expected.
- [ ] Compare the 3D view with the room: the same colours?

Note the model if a colour is wrong or two brands look very different
(that's what **Match colours across brands…** is for).

## 4. Locate (1 min)

- [ ] Select everything → **Locate** (or **L**).  **You should see:** every
      light at full, open white, moving heads pointing straight out at
      the middle of their pan and tilt, no gobo, no colour.
- [ ] **Clear** (**C**) puts them back.

Note any light that Locates coloured, dark, or with a gobo in.

## 5. Movement and its speed (3 min)

Select the moving heads:

- [ ] **Move** tab: drag the pan/tilt pad slowly.  **You should see:**
      the beams follow smoothly; left on the pad is left from the
      audience, up is up.  If a head moves the other way: **Setup** tab →
      **Invert pan** / **Invert tilt**, and note it.
- [ ] **Aim at one click…** then click the dance floor in 3D.  The real
      beams land where you clicked (within a metre is good for a first go).
- [ ] Follow speed **slow**, then click two points far apart.  The real
      heads glide; the 3D glides at the same pace.  Note if the real head
      is much faster or slower (right-click → **Calibrate movement
      speed…** fixes it per model).
- [ ] **Move** tab → **Circle**.  Smooth, no jumps at the edges.  Stop it
      from the running list under it.

## 6. Lasers and fog (2 min, if you have them)

- [ ] Without arming: press a laser or fog button.  **Nothing** must come
      out.  (If anything does, stop and note it: that's the most
      important note on this list.)
- [ ] **ARM FX** (top bar).  Now the laser button fires while held and
      stops when you let go.  Fog (**SFX** tab → **Fog / haze**): **Light**
      gives fog, **Off** / **Stop** stops it.
- [ ] **KILL FX**.  Everything stops at once, and it disarms.

## 7. Blackout and getting out (2 min)

- [ ] Set a look (everything at Full, a colour, a movement effect).
- [ ] **BLACKOUT** (or **X**).  **You should see:** every light dark at
      once, including the moving heads' beams; the button flashes red.
- [ ] **BLACKOUT** again: the look comes back as it was.
- [ ] Pull the network cable for 10 seconds, then put it back.  **You
      should see:** the lights hold their last look while it's out (that's
      the node), then follow the desk again within a couple of seconds.
- [ ] **Stop** (next to LIVE) → the top bar is back to **BLIND**.

---

## What to send back

- The ticks, and a line for every "no": *light name / mode / what it did*.
- A screenshot of **Fixtures ⋯ → DMX map** and of any odd 3D view.
- The node's make and model.
