"""How a cue list plays: tracking or cue-only, move in black, cue actions,
and blind (edit without the rig seeing it).

- Tracking: a cue holds only what it changes; everything else carries on
  from the cues before it (a jump to cue 7 plays 1..7 as they add up).  A
  cue marked `block` starts afresh.  Recording "cue only" in a tracking
  list puts the old values back in the next cue, so the change stays in
  this one.
- Move in black: a light that is dark in this cue and comes on in the next
  moves, re-colours and re-gobos while dark (once this cue's fade is done),
  so it doesn't swing on in view.
- Cue actions: a cue can press buttons, run a macro, GO another list,
  start the timeline, set the tempo... when it plays.
- Blind: the programmer goes to the 3D view only; the rig keeps what it had.
  Blind-edit a cue: its values come into the programmer, 3D shows it,
  Record puts it back; leaving blind drops what wasn't recorded.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

from app.engine_base import _truthy
from app.engine_support import HTP_ROLES

# what a cue may do when it plays (nothing that edits the show)
CUE_ACTIONS = {
    "quick_press": "press a button", "macro_run": "run a macro",
    "cue_go": "GO a cue list", "playback_release": "release a cue list",
    "timeline_play": "play the timeline", "timeline_pause": "pause the timeline",
    "timeline_seek": "move the timeline", "tempo_set": "set the tempo",
    "step_fx_run": "run a step effect", "master": "set the master",
    "autopilot": "autopilot on / off", "osc_send": "send OSC (QLab, Resolume...)",
}
MAX_CUE_ACTIONS = 8


class CueModesMixin:
    # -- tracking ----------------------------------------------------------
    @staticmethod
    def _tracked(pb: dict, index: int) -> dict:
        """The values cue `index` plays: its own, or in a tracking list,
        everything the cues before it left (back to the last block)."""
        stack = pb["stack"]
        if not 0 <= index < len(stack):
            return {}
        if not pb.get("tracking"):
            return stack[index]["values"]
        start = index
        while start > 0 and not stack[start].get("block"):
            start -= 1
        out: dict[int, dict] = {}
        for cue in stack[start:index + 1]:
            for head, row in (cue.get("values") or {}).items():
                out.setdefault(int(head), {}).update(row)
        return out

    def _mib(self, pb: dict, index: int, target: dict) -> dict | None:
        """This cue's target with the next cue's position / colour / beam
        on the lights that are dark now and come on next; None: nothing."""
        stack = pb["stack"]
        if not pb.get("mib") or not stack:
            return None
        nxt = index + 1
        if nxt >= len(stack):
            if not pb["follow"].get("loop"):
                return None
            nxt = 0
        if nxt == index:
            return None
        coming = self._tracked(pb, nxt)
        by_no = {h["head_no"]: h for h in self.patch}
        out, moved = None, False
        for head_no, row in coming.items():
            head = by_no.get(int(head_no))
            if not head or "dimmer" not in (head.get("map") or []):
                continue                      # no dimmer: can't move it unseen
            if not row.get("dimmer"):
                continue
            now_row = target.get(int(head_no)) or {}
            if now_row.get("dimmer"):
                continue                      # it's on now: leave it
            pre = {r: v for r, v in row.items() if r.split("@")[0] not in HTP_ROLES}
            if not pre:
                continue
            if out is None:
                out = {h: dict(r) for h, r in target.items()}
            merged = out.setdefault(int(head_no), {"dimmer": 0})
            merged.update(pre)
            merged.setdefault("dimmer", 0)
            moved = True
        return out if moved else None

    def _a_playback_mode(self, playback=None, tracking=None, mib=None, **_):
        """How a cue list plays: `tracking` (values carry on until a cue
        changes them) or cue only; `mib` (move in black)."""
        pb = self._playback(playback if playback is not None else 1)
        if tracking is not None:
            pb["tracking"] = _truthy(tracking)
        if mib is not None:
            pb["mib"] = _truthy(mib)
        said = ("tracking" if pb.get("tracking") else "cue only") + (", move in black" if pb.get("mib") else "")
        return {"playback": pb["n"], "tracking": bool(pb.get("tracking")), "mib": bool(pb.get("mib")),
                "summary": f"PB{pb['n']}: {said}"}

    def _cue_only_fix(self, pb: dict, index: int, before: dict, values: dict) -> int:
        """Record cue-only in a tracking list: the next cue gets back what
        the changed values were, so the change stops at this cue."""
        stack = pb["stack"]
        if not pb.get("tracking") or index + 1 >= len(stack) or stack[index + 1].get("block"):
            return 0
        nxt = stack[index + 1].setdefault("values", {})
        n = 0
        for head, row in values.items():
            had = before.get(int(head)) or {}
            for role, _v in row.items():
                if role in (nxt.get(int(head)) or {}):
                    continue                  # the next cue sets it anyway
                if role in had:
                    nxt.setdefault(int(head), {})[role] = had[role]
                elif role.split("@")[0] in HTP_ROLES:
                    nxt.setdefault(int(head), {})[role] = 0
                else:
                    continue
                n += 1
        return n

    # -- cue options: block + actions -------------------------------------
    def _a_cue_set(self, playback=None, cue=None, block=None, actions=None, **_):
        """A cue's `block` (in a tracking list: start afresh here) and its
        `actions` [{"action", "args"}] - what it does as it plays."""
        pb = self._playback(playback if playback is not None else 1)
        num = int(cue or 0)
        if not 1 <= num <= len(pb["stack"]):
            raise ValueError(f"cue must be 1..{len(pb['stack'])}")
        entry = pb["stack"][num - 1]
        said = []
        if block is not None:
            if _truthy(block):
                entry["block"] = True
            else:
                entry.pop("block", None)
            said.append("block" if entry.get("block") else "tracks")
        if actions is not None:
            clean = self._clean_cue_actions(actions)
            if clean:
                entry["actions"] = clean
            else:
                entry.pop("actions", None)
            said.append(f"{len(clean)} action(s)")
        if not said:
            raise ValueError("give block or actions")
        return {"playback": pb["n"], "cue": num, "block": bool(entry.get("block")),
                "actions": entry.get("actions") or [], "summary": f"cue {num}: " + ", ".join(said)}

    @staticmethod
    def _clean_cue_actions(actions) -> list[dict]:
        if not isinstance(actions, list):
            raise ValueError("actions is a list of {action, args}")
        out = []
        for a in actions[:MAX_CUE_ACTIONS]:
            if not isinstance(a, dict):
                continue
            name = str(a.get("action") or "")
            if name not in CUE_ACTIONS:
                raise ValueError(f"a cue can't {name!r}: it can " + ", ".join(sorted(CUE_ACTIONS)))
            args = a.get("args") if isinstance(a.get("args"), dict) else {}
            out.append({"action": name, "args": {str(k): v for k, v in args.items()
                                                 if isinstance(v, (int, float, str, bool)) or v is None}})
        return out

    def _run_cue_actions(self, pb: dict, cue: dict) -> list[str]:
        """Play a cue's actions; errors are reported, never raised (the cue
        has gone already).  A cue that GOes a list that GOes it back stops
        after a few rounds."""
        acts = cue.get("actions")
        if not acts:
            return []
        depth = self.__dict__.get("_cue_act_depth", 0)
        if depth >= 3:
            return [f"cue {cue['n']}: actions stopped (a loop of cues)"]
        self._cue_act_depth = depth + 1
        errors = []
        try:
            for a in acts:
                try:
                    getattr(self, "_a_" + a["action"])(**a["args"])
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(f"{CUE_ACTIONS.get(a['action'], a['action'])}: {exc}")
        finally:
            self._cue_act_depth = depth
        return errors

    # -- blind -------------------------------------------------------------
    def _live_programmer(self, now: float) -> dict:
        """The programmer the rig gets: the live one kept while blind."""
        live = self.__dict__.get("_blind_live")
        return live if live is not None else self._programmer_now(now)

    def blind_public(self) -> dict:
        b = self.__dict__.get("_blind_cue")
        return {"on": self.__dict__.get("_blind_live") is not None,
                "playback": b[0] if b else None, "cue": b[1] if b else None}

    def _blind_fx_from(self):
        """Effects with a higher id were started in blind: 3D only (but see
        _blind_hidden)."""
        return self.__dict__.get("_blind_fx") if self.__dict__.get("_blind_live") is not None else None

    @staticmethod
    def _blind_hidden(row: dict, seq) -> bool:
        """Started in blind from the programmer side.  A cue's effects, a
        quick button's and a timeline clip's play on the live rig even in
        blind - those reach the wire."""
        return seq is not None and row["id"] > seq and not (row.get("cue_pb") or row.get("live"))

    def _fx_live(self, fid) -> None:
        for f in self.fx:
            if f["id"] == fid:
                f["live"] = True

    def _a_blind(self, state=None, playback=None, cue=None, keep=False, **_):
        """Blind: the programmer and any effect started now show in 3D
        only - the rig keeps what it has.  With a cue: that cue's values
        come into the programmer to edit (Record puts them back).  Off:
        the programmer is as it was before and the blind effects stop -
        anything not recorded is dropped; `keep`: the rig gets all of it
        (a previewed copilot plan, applied)."""
        on = self.__dict__.get("_blind_live") is not None
        want = (not on) if state is None and cue is None else (True if cue is not None else _truthy(state))
        if want:
            if not on:
                import time as _t
                self._blind_live = {h: dict(r) for h, r in self._programmer_now(_t.monotonic()).items()}
                self._blind_prog = {h: dict(r) for h, r in self.programmer.items()}
                self._blind_fx = self._fx_seq
            self._blind_cue = None
            if cue is not None:
                pb = self._playback(playback if playback is not None else 1)
                num = int(cue)
                if not 1 <= num <= len(pb["stack"]):
                    raise ValueError(f"cue must be 1..{len(pb['stack'])}")
                self.programmer = {int(h): dict(r) for h, r in (pb["stack"][num - 1].get("values") or {}).items()}
                self._prog_fade = None
                self._blind_cue = (pb["n"], num)
                return {"blind": self.blind_public(),
                        "summary": f"preview: editing cue {num} on PB{pb['n']} in 3D only - the rig doesn't see it; Record to keep"}
            return {"blind": self.blind_public(), "summary": "preview: the programmer shows in 3D only"}
        if on and _truthy(keep):
            self._blind_live = self._blind_prog = self._blind_cue = None
            return {"blind": self.blind_public(), "summary": "preview applied: the rig has it now"}
        if on:
            self.programmer = self.__dict__.get("_blind_prog") or {}
            self._prog_fade = None
            seq = self.__dict__.get("_blind_fx")
            if seq is not None:
                self.fx = [r for r in self.fx if not self._blind_hidden(r, seq)]
        self._blind_live = None
        self._blind_prog = None
        self._blind_cue = None
        return {"blind": self.blind_public(), "summary": "preview off: the programmer is live again"}

    def _blind_recorded(self, pb: dict, num: int) -> None:
        """Recording while blind-editing that cue ends the edit (the rest
        of blind stays on)."""
        if self.__dict__.get("_blind_cue") == (pb["n"], num):
            self._blind_cue = None
