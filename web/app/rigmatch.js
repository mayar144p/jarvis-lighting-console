// Pure helpers for matching the desk to the rig in front of it (no screen
// here, so the selftest runs them under Node):
//   kindOf / pairUp     venue check-in: which light found here can take the
//                       place of a light of the show that isn't here
//   connectionChips     what the desk is connected to, for the status bar

// in this order: "Mega Par Profile" is a PAR, "Spot Bar" a moving bar...
const KINDS = [["par", /\bpar\b|par\s?\d|slimpar|flat\s?par/i], ["beam", /beam|sharpy/i], ["spot", /spot|profile|hybrid|source\s?four|ellipsoidal/i],
  ["wash", /wash|aura|zoom/i], ["bar", /\bbar\b|batten|strip|pix/i], ["strobe", /strobe|atomic|blinder/i],
  ["laser", /laser/i], ["fog", /fog|haze|smoke/i], ["panel", /panel|matrix/i]];

/** A light's kind from its name ("Chauvet SlimPAR Q12" -> par), for pairing. */
export function kindOf(name) {
  const k = KINDS.find(([, re]) => re.test(String(name || "")));
  return k ? k[0] : "light";
}

/** Pair each silent patched light with a new light of the same kind (then
 *  the same channel count), each new light used once.  [{head, dev}] */
export function pairUp(silent, found) {
  const left = [...found];
  const out = [];
  const take = (pred) => {
    for (const s of silent) {
      if (out.some((p) => p.head.head === s.head)) continue;
      const i = left.findIndex((d) => pred(s, d));
      if (i >= 0) out.push({ head: s, dev: left.splice(i, 1)[0] });
    }
  };
  take((s, d) => kindOf(s.model || s.name) === kindOf(d.name) && (!s.channels || !d.footprint || s.channels === d.footprint));
  take((s, d) => kindOf(s.model || s.name) === kindOf(d.name));
  return out;
}

export function connectionChips(s, controllers = []) {
  const chips = [];
  const t = s.tempo || {};
  if (t.prodj) {
    chips.push(t.decks
      ? { k: "ok", text: `CDJs ×${t.decks}` + (t.master_deck ? ` · master ${t.master_deck}` : ""), tip: "Pro DJ Link players on the network", tab: "songs" }
      : { k: "warn", text: "CDJs: none heard", tip: "Listening for Pro DJ Link, but no player has spoken (same network? Link cable?)", tab: "songs" });
  }
  if (t.link) chips.push({ k: t.link.peers ? "ok" : "warn", text: `Link ${t.link.peers} peer${t.link.peers === 1 ? "" : "s"}`, tip: "Ableton Link", tab: "midi" });
  if (t.source === "midi") chips.push({ k: "ok", text: "MIDI clock", tip: "the tempo follows MIDI clock", tab: "midi" });
  for (const c of controllers) chips.push({ k: "ok", text: c.layout || c.name, tip: `${c.name}: pads play the buttons, faders the playbacks`, tab: "midi" });
  const o = s.osc || {};
  if (o.on) chips.push({ k: o.error ? "bad" : "ok", text: `OSC in${o.count ? ` · ${o.count}` : ""}`, tip: o.error || `listening on UDP ${o.port} (Stream Deck, Companion, TouchOSC)`, tab: "midi" });
  const hue = s.hue || {};
  if (hue.paired) chips.push({ k: hue.error ? "bad" : "ok", text: `Hue ${hue.links}`, tip: hue.error || `${hue.links} lamp(s) follow the desk`, tab: "hue" });
  const sb = s.standby || {};
  if (sb.following) chips.push({ k: sb.error ? "bad" : "ok", text: "Backup desk", tip: sb.error || `following ${sb.host}, output off`, tab: "standby" });
  else if (sb.took_over) chips.push({ k: "bad", text: "Took over", tip: "this backup desk drives the lights now", tab: "standby" });
  const sg = s.songs || {};
  if (sg.follow) chips.push({ k: "ok", text: "Songs follow decks", tip: "songs start with their tracks on the master deck", tab: "songs" });
  return chips;
}

