"""The command line and attribute ranges.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import re

from app.engine_base import (
    PALETTE_KINDS,
    UNDO_EXCLUDED,
    UNDO_LIMIT,
    _attr_role,
    _clamp,
    _deg,
    _is_int,
    _is_num,
    _logical_to_phys,
    _num,
    _phys_to_logical,
    _similar,
    attr_domain,
)
from app.engine_support import ATTRIBUTE_ALIAS as _ATTRIBUTE_ALIAS
from app.engine_support import HTP_ROLES
from app.merge import FX_OUTPUT_ROLES


class CommandMixin:
    # ------------------------------------------------------------------
    # -- the command line ---------------------------------------------
    # ------------------------------------------------------------------
    #
    # THE LAST PIECE OF THE OPERATOR LOOP.  Every control the console has
    # can be reached with the mouse, which is fine until you want to do
    # the same thing to a second group of heads, or you know the fixture
    # is 90 degrees out and you do not want to drag an encoder to find out
    # which way.  A command line is not a scripting gimmick: on grandMA,
    # MagicQ and Eos it is the FASTEST way to do ordinary work, and
    # operators build muscle memory for it.
    #
    # It lives in the engine, not the client, for one reason: there would
    # otherwise be two parsers - one in JavaScript for the console and one
    # for the agent and the HTTP API - and they would disagree about
    # exactly the ambiguous cases.  `1-4 pan 90` has to mean one thing.
    #
    # A LINE IS ONE UNDO STEP AND IS ALL-OR-NOTHING.  Typing
    # `1-4 pan 90 red 255` and having pan apply when red fails leaves the
    # rig in a state nobody asked for and cannot predict from the
    # transcript.  So the line is parsed before anything is touched, and
    # if any step fails the whole line is rolled back to where it started.
    # A `cue go` line pushes NO undo step at all, because firing a cue is
    # an event rather than an edit - the same reason cue_go is excluded
    # from the button path.
    #
    # A bare number followed by `go` is a CUE, not a head.  `1-4 go` would
    # be one head and go to nothing; `3 go` firing cue 3 is what the
    # operator meant, and a console that made them say `cue 3 go` every
    # time would be typed at and sworn at.

    # `aim` / `at` / `position` are three spellings of one verb, so they
    # are named once here and folded into CMD_VERBS below.  A console's own
    # vocabulary is `At`; `aim` is the plain-English one.
    CMD_AIM = ("aim", "at", "position")

    CMD_VERBS = frozenset({
        "go", "back", "cue", "group", "palette", "preset", "master",
        "blackout", "clear", "home", "record", "thru", "off", "help",
        "fan", "select", "store", "align", "distribute", "mirror",
    } | set(CMD_AIM))

    # What may BEGIN a line.  A verb needs no selection of its own; a
    # selection keyword IS one.  Getting this wrong is why `all` answered
    # "nothing is selected" on an empty rig - it was skipped as a keyword
    # and then never read as the selection it is.
    CMD_START = CMD_VERBS | {"all", "none", "*"}

    def _cmd_help(self) -> str:
        return (
            "selection    1-4   1.3.5   all   none   *   group 3\n"
            "attributes   1-4 pan 90   1-4 dimmer 50   1-4 red 255\n"
            "             1-4 tilt off   1-4 wheel full   1-4 dimmer +10\n"
            "cues         go   back   cue 3 go   cue 3 at 50   3 at 50\n"
            "recording    record   record 2   1-4 clear   clear\n"
            "looks        palette colour 2   preset 1\n"
            "aiming       1-4 aim pan 90   1-4 aim 90 -30   1-4 at 90\n"
            "arranging    align x   distribute z   mirror x   mirror x about 0\n"
            "desk         master 60   master full   blackout on\n"
            "A value of `off` REMOVES the attribute; `+N`/`-N` move it by.\n"
            "`pan 90` is 90 DEGREES on a fixture whose file says its travel;\n"
            "with no range known it is a plain 0-255 value.\n"
            "align puts every head on ONE line; distribute spaces them\n"
            "EVENLY between the two outermost; mirror flips the shape.")

    @staticmethod
    def _cmd_tokens(text: str) -> list[str]:
        return [t for t in str(text or "").replace(",", " ").split() if t]

    def _cmd_selection(self, tokens: list[str], i: int) -> tuple[set, int]:
        """Read a leading selection, or return the current one.

        Returns the heads to act on and the token index reached, AND
        whether the operator actually named heads in this line.

        The flag matters because the alternative - "did it find any heads" -
        cannot tell `1-4` (a real selection) from `group 3` naming a group
        that is now empty, and silently acting on the current selection
        instead is how a command line fires the wrong thing.

        A selection is a RANGE (`1-4`), a LIST (`1.3.5`, which is how every
        console spells "heads 1, 3 and 5" without commas), `all`, `none`,
        `*`, or `group N`.
        """
        if i >= len(tokens):
            return (set(self.selected), i, False)
        tok = tokens[i].lower()
        if tok in ("all", "*"):
            if not self.patch:
                raise ValueError("nothing is patched yet")
            return ({h["head_no"] for h in self.patch}, i + 1, True)
        if tok in ("none", "home"):
            return (set(), i + 1, True)
        if tok == "group":
            num = tokens[i + 1] if i + 1 < len(tokens) else None
            if num is None:
                raise ValueError("group needs a number: `group 3`")
            if not _is_int(num):
                raise ValueError(f"group {num!r} is not a number")
            g = next((x for x in self.groups if int(x["n"]) == int(num)), None)
            if g is None:
                have = ", ".join(str(x["n"]) for x in self.groups) or "none"
                raise ValueError(f"no group {num} - you have {have}")
            return ({int(h) for h in g["heads"]}, i + 2, True)
        head = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-(\d+))?", tok)
        if head:
            if head.group(2):
                lo, hi = sorted((int(head.group(1)), int(head.group(2))))
                wanted = set(range(lo, hi + 1))
            else:
                wanted = {int(p) for p in head.group(1).split(".")}
            # Name the head numbers that do not exist, COUNTED rather than
            # listed.  `1-999` on a 5-head rig printing 994 numbers tells
            # the operator nothing they can act on and buries the message.
            patched = {h["head_no"] for h in self.patch}
            missing = sorted(wanted - patched)
            if missing:
                if len(missing) > 8:
                    raise ValueError(
                        f"heads not patched: {missing[0]}-{missing[-1]} "
                        f"({len(missing)} of them; this rig has heads "
                        f"{min(patched)}-{max(patched)})")
                raise ValueError("heads not patched: "
                                 + ", ".join(str(m) for m in missing))
            return (wanted, i + 1, True)
        return (set(self.selected), i, False)

    def _cmd_value(self, tok: str, role: str) -> tuple[str, float | None]:
        """`off`, `full`, `+N`, `-N` and plain numbers.

        Relative values are here because "these are 10% brighter" is a
        thing operators say out loud, and it is the one adjustment a
        console cannot do for you by dragging a fader to an absolute
        position.
        """
        text = str(tok or "").strip().lower()
        if text in ("off", "0%") and text == "off":
            return ("off", None)
        if text in ("full", "100%", "on"):
            return ("full", None)
        if text[0] in "+-":
            try:
                return ("add", float(text))
            except ValueError:
                raise ValueError(f"{text!r} needs a number after the sign"
                                 ) from None
        try:
            return ("set", float(text))
        except ValueError:
            raise ValueError(
                f"{tok!r} is not a value - use a number, off, full, "
                f"or +N / -N to move by") from None

    def _a_run_command(self, text=None, command=None, dry=False, **_):
        """Run one typed line.  See the note above the verb table."""
        line = str(text if text is not None else command or "").strip()
        if not line:
            return {"transcript": [], "summary": "nothing typed",
                    "ok": True, "steps": []}
        if line.startswith("?") or line.lower() in ("help", "?"):
            return {"help": self._cmd_help(), "transcript": [],
                    "steps": [], "ok": True,
                    "summary": "command syntax"}
        # A trailing `.` is the console terminator and is not a token.
        tokens = self._cmd_tokens(line)
        while tokens and tokens[-1] == ".":
            tokens.pop()
        if not tokens:
            return {"transcript": [], "steps": [], "ok": True,
                    "summary": "nothing typed"}

        # ---- PARSE.  Nothing below this line mutates until PLAN is built.
        plan: list[tuple[str, dict]] = []
        note_lines: list[str] = []
        i = 0
        heads: set = set()

        first = tokens[0].lower()
        # A bare cue number: `3 go` is a cue, because one head cannot go.
        cue_shortcut = False
        if re.fullmatch(r"\d+", first) and len(tokens) >= 2 \
                and tokens[1].lower() in ("go", "at", "back"):
            cue_shortcut = True
        # `all`, `none` and `*` ARE selections, so they go through the
        # reader.  `group N` too - but a bare `group` with no number has to
        # reach the verb section to say "group needs a number", because
        # otherwise the failure reads as "incomplete" and sends the
        # operator looking for a syntax problem that is not there.
        if first == "group" and len(tokens) == 1:
            raise ValueError("group needs a number: `group 3`")
        named_sel = False
        if not cue_shortcut and first not in self.CMD_START:
            heads, i, named_sel = self._cmd_selection(tokens, 0)
        elif first in ("all", "none", "*"):
            heads, i, named_sel = self._cmd_selection(tokens, 0)
        elif first == "group" and len(tokens) > 1:
            heads, i, named_sel = self._cmd_selection(tokens, 0)
        else:
            heads, i = set(self.selected), 0

        if i >= len(tokens):
            if named_sel and not cue_shortcut:
                # `1-4` or `all` on its own is a selection, and a useful
                # one.  `none` on its own means deselect all.
                plan.append(("select_heads", {"heads": sorted(heads)}))
                return self._cmd_finish(line, plan, note_lines, dry)
            raise ValueError("incomplete: " + self._cmd_help())

        tok = tokens[i].lower()
        # A selection NAMED in this line always applies.  Without this,
        # `1-2 tilt off` on two heads that have no tilt would fall through
        # to "nothing is selected" and quietly do something else - the
        # named heads are gone from `heads` only because they lack the
        # channel, which is not the same as not having been asked for.
        if named_sel:
            plan.append(("select_heads", {"heads": sorted(heads)}))
        if not heads and not cue_shortcut and tok not in (
                "go", "back", "master", "blackout", "record", "clear",
                "home", "thru", "help", "select"):
            raise ValueError(
                "nothing to act on - no patched head matches"
                if named_sel else "nothing is selected - name some heads first")

        # ---- selection verbs ------------------------------------------
        if tok in ("home", "none", "*"):
            if not plan:
                plan.append(("select_heads", {"heads": []}))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok == "clear" and len(tokens) == i + 1:
            plan.append(("clear_programmer", {}))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok in ("record", "store"):
            if len(tokens) == i + 2 and _is_int(tokens[i + 1]):
                plan.append(("record_cue", {"cue": int(tokens[i + 1])}))
            else:
                plan.append(("record_cue", {}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- cues -------------------------------------------------------
        if tok == "cue" or cue_shortcut:
            if cue_shortcut:
                num, i = int(first), 1
            else:
                if i + 1 >= len(tokens) or not _is_int(tokens[i + 1]):
                    raise ValueError("cue needs a number: `cue 3 go`")
                num, i = int(tokens[i + 1]), i + 2
            at = None
            if i < len(tokens) and tokens[i].lower() == "at":
                if i + 1 >= len(tokens):
                    raise ValueError("`cue 3 at` needs a percentage")
                at = _num(tokens[i + 1], "a percentage")
                i += 2
            params_cue: dict = {"cue": num}
            if at is not None:
                params_cue["at"] = at
            plan.append(("cue_go", params_cue))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok in ("go", "back"):
            plan.append(("cue_go" if tok == "go" else "cue_back", {}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- looks ------------------------------------------------------
        if tok in ("palette", "preset"):
            if tok == "preset":
                if i + 1 >= len(tokens) or not _is_int(tokens[i + 1]):
                    raise ValueError("preset needs a number: `preset 1`")
                plan.append(("include_preset", {"n": int(tokens[i + 1])}))
                return self._cmd_finish(line, plan, note_lines, dry)
            # A palette is per attribute FAMILY, so `palette 2` is genuinely
            # ambiguous.  It is allowed when exactly one family has a
            # palette 2, which is the usual case on a small rig, and
            # refused with the families listed when it is not.
            nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            if _is_int(nxt):
                num = int(nxt)
                have = [k for k in PALETTE_KINDS
                        if any(p["n"] == num for p in self.palettes.get(k, []))]
                if len(have) == 1:
                    plan.append(("include_palette", {"kind": have[0],
                                                     "n": num}))
                    return self._cmd_finish(line, plan, note_lines, dry)
                if not have:
                    raise ValueError(
                        f"no palette {num} - you have "
                        + (", ".join(f"{k} "
                                     + ",".join(str(p['n']) for p
                                                in self.palettes.get(k, []))
                                     for k in PALETTE_KINDS
                                     if self.palettes.get(k)) or "none"))
                raise ValueError(
                    f"palette {num} exists in {len(have)} families "
                    f"({', '.join(have)}) - say which: "
                    f"`palette {have[0]} {num}`")
            if nxt not in PALETTE_KINDS:
                raise ValueError(
                    f"palette needs a family and a number: `palette colour 2`"
                    f" - families are {', '.join(sorted(PALETTE_KINDS))}")
            if i + 2 >= len(tokens) or not _is_int(tokens[i + 2]):
                raise ValueError(f"palette {nxt} needs a number: "
                                 f"`palette {nxt} 2`")
            plan.append(("include_palette", {"kind": nxt,
                                             "n": int(tokens[i + 2])}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- aim: `1-4 aim pan 90 tilt -30`, or `1-4 at 90,128` -----------
        # Degrees when the fixture knows its travel, logical otherwise -
        # and the server decides which, not this parser, because only it
        # knows the fixture.  So both readings are sent and the transcript
        # says which one was used.
        if tok in self.CMD_AIM or (
                len(tokens) == i + 3 and re.fullmatch(r"[-+]?[\d.]+",
                                                      tokens[i + 1] or "")
                and re.fullmatch(r"[-+]?[\d.]+", tokens[i + 2] or "")):
            pos: dict = {}
            i += 1
            # Words and bare numbers both work, and the bare form is
            # positional: `aim 90 -30` is pan then tilt, because that is
            # the order every console prints them in.
            role_order: list[str] = []
            while i < len(tokens) and len(role_order) < 2:
                word = tokens[i]
                low = word.lower()
                if low in ("pan", "tilt"):
                    if i + 1 >= len(tokens):
                        raise ValueError(f"{low} needs a value")
                    pos[low] = _num(tokens[i + 1], "a value")
                    role_order.append(low)
                    i += 2
                    continue
                if _is_num(word):
                    slot = role_order[0] if role_order else None
                    if slot is None:
                        pos["pan"] = _num(word, "a value")
                        role_order.append("pan")
                    elif slot == "pan" and "tilt" not in pos:
                        pos["tilt"] = _num(word, "a value")
                        role_order.append("tilt")
                    else:
                        break
                    i += 1
                    continue
                break
            if not pos:
                raise ValueError(
                    "aim needs a pan and/or tilt: `1-4 aim pan 90`, "
                    "`1-4 aim 90 -30` (pan then tilt)")
            if i < len(tokens):
                raise ValueError(
                    f"unexpected {tokens[i]!r} - aim takes pan and tilt only")
            if named_sel:
                plan.append(("select_heads", {"heads": sorted(heads)}))
            # `unit` is NOT sent.  Only the server knows whether this
            # fixture's travel is in degrees, and a client-side guess would
            # be exactly the silent reinterpretation §17.19 exists to
            # prevent.  The transcript says which reading was used.
            plan.append(("set_position", dict(pos)))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- arranging on stage -----------------------------------------
        # The same three words every console uses, and the distinction
        # between them is the whole point: ALIGN puts every head on one
        # line, DISTRIBUTE spaces them evenly between the two outermost,
        # MIRROR flips the shape.  A row of six with the middle two
        # bunched is not misaligned, so aligning is the wrong tool and
        # treating the two as synonyms is how these features go wrong.
        if tok in ("align", "distribute", "mirror"):
            action = {"align": "align", "distribute": "distribute",
                      "mirror": "mirror"}[tok]
            params_arr: dict = {}
            if named_sel:
                params_arr["heads"] = sorted(heads)
            axis_tok = tokens[i + 1] if i + 1 < len(tokens) else "x"
            params_arr["axis"] = axis_tok
            nxt = i + 2
            if action == "mirror" and nxt < len(tokens) \
                    and tokens[nxt].lower() == "about":
                if nxt + 1 >= len(tokens):
                    raise ValueError("`mirror x about` needs a value")
                params_arr["about"] = _num(tokens[nxt + 1],
                                           "a position in metres")
                nxt += 2
            elif nxt < len(tokens) and _is_int(tokens[nxt]):
                # A bare number after the axis is the centre for mirror,
                # and is meaningless for align/distribute - which do not
                # take a centre, so say so rather than ignoring it.
                if action == "mirror":
                    params_arr["about"] = _num(tokens[nxt], "a position")
                else:
                    raise ValueError(
                        f"{tok} does not take a number - it works on the "
                        f"selection as it stands "
                        f"(to centre a mirror, use `mirror x about N`)")
                nxt += 1
            if nxt < len(tokens):
                raise ValueError(f"unexpected {tokens[nxt]!r}")
            plan.append((action, params_arr))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- desk ------------------------------------------------------
        if tok == "master":
            word = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            plan.append(("master", {"level":
                            100.0 if word in ("full", "on", "100")
                            else (0.0 if word in ("off", "0")
                                  else _num(word, "a level 0-100"))}))
            return self._cmd_finish(line, plan, note_lines, dry)
        if tok == "blackout":
            word = tokens[i + 1].lower() if i + 1 < len(tokens) else "toggle"
            if word not in ("on", "off", "toggle"):
                raise ValueError("blackout takes on, off, or nothing")
            plan.append(("blackout", {"state":
                            (not self.blackout) if word == "toggle"
                            else int(word == "on")}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- `1.3 clear` / `1-4 off` : clear the programmer on heads -----
        if len(tokens) == i + 1 and tok == "clear":
            plan.append(("clear_heads", {"heads": sorted(heads)}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- a colour by name: `1-4 red`, `all deep blue`, `3 #ff8800` -
        colour_text = " ".join(tokens[i:])
        from app import showdesign as _sd
        hexcode = _sd._to_hex(colour_text) if not _is_num(colour_text) else None
        if hexcode and (len(tokens) == i + 1 or _attr_role(tok) is None):
            plan.append(("set_colour", {"hex": hexcode}))
            return self._cmd_finish(line, plan, note_lines, dry)

        # ---- attributes -------------------------------------------------
        role = _attr_role(tok)
        if role is None:
            near = self._cmd_near(tok, list(_ATTRIBUTE_ALIAS))
            raise ValueError(
                f"do not know {tok!r}" + (f" - did you mean {near}?"
                                          if near else "")
                + "\n" + self._cmd_help())
        if i + 1 >= len(tokens):
            raise ValueError(f"{role} needs a value: `{role} 0`, "
                             f"`{role} off`, `{role} +10`")
        leftover = tokens[i + 2:]
        if leftover:
            # A console takes a trailing `.` as the terminator - `1-4 pan 90 .`
            # is normal - but anything else is a second command typed without
            # separating it, and guessing which to run would be worse than
            # saying so.
            raise ValueError(
                f"unexpected {leftover[0]!r} after the value - one command "
                f"per line (press Enter between them)")
        how, val = self._cmd_value(tokens[i + 1], role)
        rng = self.role_range([h for h in self.patch
                               if h["head_no"] in heads], role)
        params: dict = {"attribute": role}
        if how == "off":
            # `off` REMOVES the attribute rather than setting it to zero.
            # They are not the same thing: a palette value underneath shows
            # through a removed attribute and is overwritten by a zero, so
            # `off` has to be the one that means "let go of it".
            #
            # An attribute that was never set is a NO-OP, not an error.
            # `1-2 tilt off` where neither head has a tilt in the programmer
            # is the operator tidying up, and answering "it was not set" is
            # the sort of pedantry that teaches people not to type.
            if not any((self.programmer.get(h["head_no"]) or {}).get(role)
                       for h in self.patch if h["head_no"] in heads):
                note_lines.append(f"{role} was not set — nothing to remove")
                plan.append(("select_heads", {"heads": sorted(heads)}))
                return self._cmd_finish(line, plan, note_lines, dry)
            params["clear"] = True
        elif how == "full":
            params["value"] = 100 if role in HTP_ROLES else (
                min([attr_domain(h, role) for h in self.patch
                     if h["head_no"] in heads] or [255]))
        elif how == "add":
            params["value"] = val
            params["relative"] = True
            if rng.get("unit") == "degree":
                params["unit"] = "degree"
        else:
            params["value"] = val
            if rng.get("unit") == "degree" and val is not None \
                    and abs(val) <= 360:
                # `1-4 pan 90` means 90 DEGREES on a head that says its
                # travel is in degrees.  The unit is still sent explicitly
                # rather than inferred here - the server does the deciding
                # and the transcript says which reading was used.
                params["unit"] = "degree"
        plan.append(("set_attr_range", params))
        return self._cmd_finish(line, plan, note_lines, dry)

    @staticmethod
    def _cmd_near(word: str, pool: list[str]) -> str:
        """The closest known word, if one is close enough to be a typo."""
        w = re.sub(r"[^a-z0-9]", "", str(word or "").lower())
        best, score = "", 0.0
        for cand in pool:
            s = _similar(w, re.sub(r"[^a-z0-9]", "", cand))
            if s > score:
                best, score = cand, s
        return best if score >= 0.6 else ""

    def _cmd_finish(self, line: str, plan: list, notes: list,
                    dry: bool) -> dict:
        """Execute a parsed plan as ONE atomic, ONE-undo-step change."""
        mutating = [p for p in plan if p[0] not in UNDO_EXCLUDED]
        if dry:
            return {"text": line, "steps": [{"action": a, "params": p}
                                            for a, p in plan],
                    "transcript": [], "ok": True, "dry": True,
                    "summary": f"would run {len(plan)} step(s)",
                    "help": self._cmd_help()}
        before = self._undo_state() if mutating else None
        transcript = []
        # The interesting fields of each step (`clipped`, `partial`,
        # `missing`, ...) are surfaced so a client can MARK the heads
        # involved rather than re-reading a sentence.  Later steps win,
        # since the last thing a line did is what the operator is looking
        # at.
        extra: dict = {}
        try:
            for action, params in plan:
                handler = self._handlers.get(action)
                if handler is None:
                    raise ValueError(f"unknown action {action!r}")
                res = handler(**params) or {}
                if res.get("ok") is False:
                    raise ValueError(res.get("error")
                                     or f"{action} failed")
                said = res.get("summary")
                if said:
                    transcript.append(said)
                extra.update({k: v for k, v in res.items()
                              if k not in ("ok", "summary", "action",
                                           "engine", "simulated", "error")})
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            # A line either does what it says or nothing.  Rolling back
            # rather than leaving the first half applied is what makes the
            # transcript trustworthy.
            #
            # Selection is restored too, and deliberately: `1-2 dimmer 50`
            # changes WHICH HEADS are selected as a side effect, and a
            # failed line that quietly left the selection moved would mean
            # the next line acts on heads nobody chose.
            if before is not None:
                self._restore_state(before)
            raise ValueError(
                f"{exc} — nothing from this line was applied"
                if mutating else str(exc)) from None
        if mutating:
            # The state BEFORE the line, pushed as ONE step, and labelled
            # with the line itself so the undo button reads
            # "undo: 1-4 pan 90" rather than "undo".  The label is the
            # whole point of that button - a button that might undo
            # something is worse than no button.
            self._undo.append({"action": "command", "label": line,
                               "state": before, "at": self._clock()})
            if len(self._undo) > UNDO_LIMIT:
                del self._undo[0]
            self._undo_label = line
            self._redo.clear()
            self._redo_label = ""
            self._autosave()
        self._sync_follow_thread()
        # The vocabulary the client's tab-completion offers, so it can never
        # suggest a command this parser does not have.  It arrives with
        # every run, so a change here shows up in the box immediately.
        return {
            "text": line,
            "steps": [{"action": a, "params": p} for a, p in plan],
            "transcript": transcript,
            "notes": notes,
            "ok": True,
            "selection": sorted(self.selected),
            **extra,
            "cmd_verbs": sorted(self.CMD_VERBS),
            "summary": (" · ".join(transcript) or line) + (
                "   [" + " · ".join(notes) + "]" if notes else ""),
        }

    def _a_set_attr_range(self, attribute=None, role=None, value=None,
                          unit=None, clear=False, relative=False, **_):
        """Set one attribute across a selection, reporting partial writes.

        This is what an encoder row calls.  It exists rather than reusing
        `set_attribute` because an encoder's promise is "this knob now
        says X" and `set_attribute` silently did nothing to a head that
        lacks the channel - so a fader row would show a value that only
        some of the rig received, with nothing on screen to say so.

        `unit="degree"` converts the value through the FIXTURE'S OWN
        range.  Without it an operator aiming a moving head types 18000
        and has to know that means 0 degrees on a 540-degree pan and
        nothing at all on the next light in the row.  The unit is
        EXPLICIT: with no `unit` the number is logical (0-100 for a level,
        0-65535 otherwise), which is what every other caller means.
        """
        want = str(attribute or role or "").strip().lower()
        resolved = _attr_role(want)
        if resolved is None:
            raise ValueError(f"unknown attribute {want!r}")
        if value is None and not (clear or relative):
            raise ValueError("a value is required")
        try:
            asked = 0.0 if value is None else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"not a number: {value!r}") from None
        if resolved in FX_OUTPUT_ROLES and not (resolved == "laser_on" and clear):
            raise ValueError(f"{resolved} is an effect's output: use the armed FX buttons")
        heads = self._require_selection()
        capable = [h for h in heads if resolved in (h.get("map") or [])]
        if not capable:
            have = sorted({r for h in heads for r in (h.get("map") or [])
                           if r not in ("raw", "unused")})
            raise ValueError(
                f"none of the selected heads has a {resolved} channel "
                f"(they have: {', '.join(have) or 'nothing controllable'})")
        full = min([attr_domain(h, resolved) for h in capable] or [255])

        # --- `off`: REMOVE the attribute -----------------------------------
        # Not "set it to zero".  A zero is a value that overrides whatever
        # the palette or playback underneath was doing, whereas removing it
        # lets that show through again - which is what a console's OFF
        # means, and the difference is visible the moment anything else is
        # driving the same heads.
        if clear:
            removed, untouched = [], []
            for h in capable:
                row = self.programmer.get(h["head_no"])
                if row and resolved in row:
                    row.pop(resolved, None)
                    removed.append(h["head_no"])
                    if not row:
                        self.programmer.pop(h["head_no"], None)
                else:
                    untouched.append(h["head_no"])
            if not removed:
                raise ValueError(
                    f"{resolved} was not set on "
                    + (", ".join(str(h['head_no']) for h in capable)
                       or "any selected head"))
            return {"attribute": resolved, "cleared": removed,
                    "heads": len(capable), "no_op": untouched,
                    "summary": (f"{resolved} removed from {len(removed)} of "
                                f"{len(capable)} head(s)")}

        # --- physical units ------------------------------------------------
        # A role in HTP_ROLES is a 0-100 LEVEL and never has a physical
        # range; asking for degrees on one is a mistake worth naming
        # rather than silently clamping.
        #
        # `unit` is EXPLICIT or the value is logical.  Guessing is the trap
        # here: "90" means 90 of 65535 to a script and 90 DEGREES to someone
        # looking at a field labelled °, and silently picking one of those
        # is how a head ends up pointing at the wall.  The client is told
        # the unit by `attribute_state` and passes it back.
        want_unit = str(unit or "").strip().lower()
        phys = want_unit in ("degree", "deg", "degrees")
        phys_note = ""
        # The domain of the NARROWEST capable channel.  A 16-bit pan pair
        # takes 0-65535 and an 8-bit one 0-255; sending 0-65535 to a
        # mixed selection clamps every 8-bit head to full and reports
        # success, which is the worst of both.
        full = min([attr_domain(h, resolved) for h in capable] or [255])
        rng = self.role_range(capable, resolved)
        if phys:
            if rng.get("mixed"):
                # Only blocks the CONVERSION.  A logical value means the
                # same thing on every head whatever the light's travel, so
                # refusing that would take away the one number that still
                # works on a mixed selection.
                raise ValueError(
                    f"the selected {resolved} channels have different ranges "
                    f"({'; '.join('%g..%g' % (lo, hi) for lo, hi in rng['mixed'])})"
                    f" — set them one at a time, or give a value in "
                    f"0-65535")
            if not rng:
                raise ValueError(
                    f"no range is known for {resolved} on this fixture, so "
                    f"degrees cannot be converted — give a 0"
                    f"{'-100' if resolved in HTP_ROLES else '-65535'} value")
            if rng.get("unit") != "degree":
                raise ValueError(
                    f"{resolved} is not measured in degrees on this fixture "
                    f"(it is a {rng.get('unit', 'raw')} value)")
            value = _phys_to_logical(asked, rng["min"], rng["max"], full)
        elif want_unit and rng.get("unit") == "degree" \
                and want_unit not in ("auto", "logical", "raw", "dmx"):
            raise ValueError(
                f"{resolved} is measured in degrees, not {want_unit}")

        # --- `+N` / `-N`: move by, per head -------------------------------
        # Each head moves from ITS OWN current value, so a mixed selection
        # that is already uneven stays uneven in the same proportions.  One
        # "old value" taken from the first head would quietly level them,
        # which is the opposite of what "these are 10% brighter" means.
        if relative:
            step = asked
            if phys:
                # A relative ANGLE: convert the offset through the same
                # range, so `tilt +10` is ten degrees on this light rather
                # than ten thousandths of its travel.
                step = _phys_to_logical(step, rng["min"], rng["max"], full)
            landed, clipped = [], []
            for h in capable:
                top = 100 if resolved in HTP_ROLES else attr_domain(h, resolved)
                was = float((self.programmer.get(h["head_no"]) or {})
                            .get(resolved, 0) or 0)
                new = _clamp(was + step, 0, top)
                # "Clipped" means the step COULD NOT BE TAKEN IN FULL, in
                # either direction.  Only checking `step > 0` misses a fall
                # that hit the bottom, which is the half an operator meets
                # first: `dimmer -100` from 30 stores 0 and must say so,
                # because the alternative reading is "you asked for -100
                # and the desk did that".
                if abs(new - (was + step)) > 0.5:
                    clipped.append(h["head_no"])
                landed.append(new)
                if resolved in HTP_ROLES:
                    for r, v in self._level_values(h, new).items():
                        self._set_programmer(h["head_no"], r, v)
                else:
                    self._set_programmer(h["head_no"], resolved, new)
            missing = [h["head_no"] for h in heads
                       if resolved not in (h.get("map") or [])]
            note = ""
            if missing:
                note += (f"; {len(missing)} head(s) have no {resolved} "
                         f"channel and were left alone")
            if clipped:
                note += ("; " + ", ".join(str(c) for c in clipped)
                         + (" hit" if len(clipped) == 1 else " hit")
                         + " the end and could not move "
                         + ("as far" if len(clipped) == 1 else "as far"))
            note += f" by {_deg(asked):g}°" if phys else f" by {step:+g}"
            return {"attribute": resolved,
                    "value": landed[0] if landed else None, "values": landed,
                    "by": step, "heads": len(capable), "missing": missing,
                    "clipped": clipped, "relative": True,
                    "summary": (f"{resolved} {step:+g} on {len(capable)} of "
                                f"{len(heads)} head(s){note}")}

        if resolved in HTP_ROLES:
            applied = _clamp(int(round(float(value))), 0, 100)
            for h in capable:
                for r, v in self._level_values(h, applied).items():
                    self._set_programmer(h["head_no"], r, v)
        else:
            applied = _clamp(int(round(float(value))), 0, full)
            for h in capable:
                self._set_programmer(h["head_no"], resolved, applied)
        missing = [h["head_no"] for h in heads
                   if resolved not in (h.get("map") or [])]
        note = (f"; {len(missing)} head(s) have no {resolved} channel and were "
                f"left alone" if missing else "")
        clamped = applied != int(round(float(value)))
        if phys:
            # The operator typed a DEGREES number, so the message has to be
            # in degrees.  Echoing the pre-conversion logical value (the
            # internal 141992 that stands for 900 on a 540-degree pan) puts
            # a number in front of them that means nothing to them, and is
            # the one case where "asked for X" reads as nonsense.
            got = _deg(_logical_to_phys(applied, rng["min"], rng["max"], full))
            if clamped:
                note += (f" (asked for {_deg(asked):g}°, clamped to {got:g}°"
                         f" — this head travels {rng['min']:g}..{rng['max']:g}°)")
            else:
                phys_note = f" = {got:g}°"
        elif clamped:
            # Report what was STORED, not what was asked for.  Echoing the
            # request would put 9999 in the encoder while the light was
            # sent 255 - the encoder would then lie until the next
            # repaint, and the operator would trust it.
            note += f" (asked for {value:g}, clamped to {applied})"
        out = {"attribute": resolved, "value": applied,
               "requested": int(round(float(value))), "clamped": clamped,
               "full": full,
               "heads": len(capable), "missing": missing,
               "partial": bool(missing)}
        if phys:
            out["unit"] = "degree"
            out["min"], out["max"] = rng["min"], rng["max"]
            out["requested_phys"] = _deg(asked)
            out["phys"] = got
        out["summary"] = (f"{resolved} = {applied} on {len(capable)} of "
                          f"{len(heads)} head(s){note}{phys_note}")
        return out
