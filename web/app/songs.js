// Songs: a light show per song.  The timeline (its music, parts and
// tracks) and the cues it plays are kept under the song's name; playing a
// song puts them back.  With Pro DJ Link a song is tied to a track on the
// CDJs and starts by itself, in step with the deck.
import { state } from "./store.js";
import { run } from "./actions.js";
import { h, modal, promptBox, confirmBox, toast } from "./ui.js";
import { post, token } from "./api.js";
import { songParts, loudness } from "./songparts.js";

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const songs = () => (state.snap && state.snap.songs) || { songs: [], follow: false };

export async function keepAsSong() {
  const d = (state.snap && state.snap.timeline) || {};
  const music = (d.audio && d.audio.name || "").replace(/\.[A-Za-z0-9]{2,4}$/, "");
  const name = await promptBox("Keep as a song", "The song's name", music || "");
  if (name === null || name === undefined) return null;
  return run("song_save", { name }, { toast: true });
}

export function openSongs() {
  let close = null;
  let latest = null;                   // the newest list an action answered with
  const body = h("div.songs-box");
  const act = (a, p, o) => run(a, p, o).then((r) => { if (r && r.songs) latest = r.songs; paint(); return r; });
  const paint = () => {
    const s = latest || songs();
    const follow = h("input", { type: "checkbox", checked: !!s.follow });
    follow.addEventListener("change", async () => {
      await act("song_follow", { state: follow.checked }, { toast: true });
    });
    const rows = s.songs.map((x) => {
      const playing = s.following === x.id || s.current === x.id;
      return h("tr", { class: playing ? "is-current" : null },
        h("td", h("b", x.name), x.music ? h("div.muted.small", "♪ " + x.music) : ""),
        h("td.muted.small", `${mmss(x.length)} · ${Math.round(x.bpm)} BPM` + (x.parts ? ` · ${x.parts} parts` : "")),
        h("td.small", x.deck ? h("span", { title: "Starts when this track plays on the master deck" }, `🎧 track ${x.track}`) : h("span.muted", "-")),
        h("td", h("div.song-acts",
          h("button.btn.small.primary", { onclick: () => act("song_load", { id: x.id, play: true }, { toast: true }) }, "Play"),
          h("button.btn.small", { title: "Put it on the timeline to change it, then Keep it again", onclick: () => act("song_load", { id: x.id }, { toast: true }) }, "Edit"),
          h("button.btn.small", { title: s.deck ? `Tie it to track ${s.deck} on the master deck` : "Play the track on the master CDJ first",
            disabled: !s.deck && !x.deck,
            onclick: () => act("song_link", x.deck && !s.deck ? { id: x.id, unlink: true } : { id: x.id }, { toast: true }) },
          x.deck && !s.deck ? "Untie" : "Tie to deck"),
          h("button.btn.small.ghost", { onclick: async () => {
            const n = await promptBox("Rename song", "Name", x.name);
            if (n) act("song_rename", { id: x.id, name: n });
          } }, "Rename"),
          h("button.btn.small.ghost", { onclick: async () => {
            if (await confirmBox("Delete song", `Delete “${x.name}”? Ctrl+Z brings it back.`, { ok: "Delete", danger: true })) act("song_delete", { id: x.id }, { toast: true });
          } }, "Delete"))));
    });
    const cur = s.songs.find((x) => x.id === s.current);
    body.replaceChildren(
      h("p.muted.small", "A song keeps its own timeline - music, parts, cues and effects - so it looks the same every night. "
        + "Make one: ♪ Audio → Add audio, Find the song's parts, then Auto show; keep it here. "
        + "Play a song, or with Pro DJ Link tie it to its track: it then starts by itself when the DJ plays it."),
      h("div.row-btns",
        h("button.btn.primary", { onclick: () => keepAsSong().then((r) => { if (r && r.songs) latest = r.songs; paint(); }) }, "Keep the timeline as a song"),
        s.songs.length ? h("button.btn", { onclick: () => { close(); openSongShows(); } }, "Make shows for songs…") : "",
        cur ? h("button.btn", { onclick: () => act("song_save", { id: cur.id }, { toast: true }) }, `Update “${cur.name}”`) : ""),
      h("label.check", follow, h("span", "Songs follow the DJ decks (Pro DJ Link)")),
      h("p.muted.small", s.follow ? (s.deck ? `The master deck is playing track ${s.deck}.` : "Waiting for the master deck to play a track.")
        : "On: when the tempo master CDJ plays a tied track, its song starts in step with the deck, at the deck's speed, and pauses with it. "
          + "The next song's look fades in from the one before (2 s)."),
      h("div.look-row", h("span", "Between songs"),
        h("div.seg", ...[["autopilot", "Autopilot"], ["hold", "Hold the last look"]].map(([k, label]) =>
          h("button" + ((s.between || "autopilot") === k ? ".on" : ""), { type: "button",
            onclick: () => act("song_follow", { state: null, between: k }) }, label))),
        h("span.muted.small", (s.between || "autopilot") === "autopilot"
          ? "a track with no song: the autopilot's looks fade in, picked by the music; the next tied track takes over again"
          : "a track with no song: the last look stays")),
      s.fallback_problem ? h("p.small.out-bad", `⚠ The autopilot can't fill in: ${s.fallback_problem}.`) : "",
      s.songs.length ? h("table.songs-table", h("tbody", ...rows)) : h("p.muted", "No songs yet."));
  };
  paint();
  close = modal({ title: "Songs", wide: true, body });
  return close;
}

// ---------------------------------------------------------------------------
// A show for each song, by the AI (or the desk's own designer): each song in
// turn goes on the timeline, its parts are found from its music when it has
// none, the design is made for this rig and these parts, built, and kept.
async function partsFromMusic(tl) {
  const a = tl.audio;
  if (!a || !a.id) return null;
  const t = token();
  const r = await fetch("/api/console/audio?id=" + encodeURIComponent(a.id), { headers: t ? { "X-Jarvis-Token": t } : {} });
  if (!r.ok) return null;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const buf = await ctx.decodeAudioData(await r.arrayBuffer());
    const parts = songParts(loudness(buf.getChannelData(0), buf.sampleRate, 20), 20, tl.bpm || 120);
    return parts.length >= 2 ? parts.map((p) => ({ t: +(p.t + (a.offset || 0)).toFixed(2), name: p.name })) : null;
  } finally {
    ctx.close();
  }
}

export function openSongShows() {
  const list = (songs().songs || []);
  const brief = h("textarea", { rows: 2, spellcheck: "false", placeholder: "e.g. dark techno in a warehouse, red and white, hard drops", style: { width: "100%" } });
  const offline = h("input", { type: "checkbox" });
  const ticks = list.map((x) => h("input", { type: "checkbox", checked: true, "data-id": x.id }));
  const log = h("div.song-log");
  const go = h("button.btn.primary", {}, "Make the shows");
  const body = h("div.songs-box",
    h("p.muted.small", "For each song: its parts from its music (when it has none yet), then a show designed for this rig and those parts - "
      + "a look per part, effects, strobe and flash hits on the beat, master moves - built and kept in the song. "
      + "The AI designs it from your words below (Settings → AI); offline, the desk's own designer does."),
    h("label.field", h("span", "What should the shows be like?"), brief),
    h("label.check", offline, h("span", "The desk's own designer (no AI)")),
    h("div.song-picks", ...list.map((x, i) => h("label.check", ticks[i], h("span", x.name + (x.parts ? ` · ${x.parts} parts` : " · no parts yet"))))),
    h("div.row-btns", go),
    log);
  const close = modal({ title: "Make shows for songs", wide: true, body });
  go.addEventListener("click", async () => {
    const ids = ticks.filter((t) => t.checked).map((t) => t.dataset.id);
    if (!ids.length) { toast("Pick a song"); return; }
    go.disabled = true;
    const say = (txt, cls = "") => { log.append(h("div.small" + cls, txt)); log.scrollTop = log.scrollHeight; };
    let made = 0;
    for (const id of ids) {
      const x = list.find((y) => y.id === id);
      say(`${x.name}…`);
      const ld = await run("song_load", { id }, { silentError: true });
      if (!ld.ok) { say(`  couldn't open it: ${ld.error}`, ".out-bad"); continue; }
      const tl = ld.timeline || {};                // this song's (the screen's copy may lag)
      if (!(tl.markers || []).length) {
        const parts = await partsFromMusic(tl).catch(() => null);
        if (parts) {
          await run("timeline_set", { markers: parts }, { silentError: true });
          say(`  parts: ${parts.map((p) => p.name).join(", ")}`);
        } else say("  no parts found (no music on it): a template song shape", ".muted");
      }
      const des = await post("/api/console/autoshow", { prompt: brief.value.trim() || "A high-energy club night.", offline: offline.checked })
        .catch((e) => ({ error: e.message }));
      const dr = des.result || des;
      if (des.error || !dr.design) { say(`  no design: ${des.error || dr.error || "?"}`, ".out-bad"); continue; }
      const built = await post("/api/console/autoshow", { apply: true, design: dr.design }).catch((e) => ({ error: e.message }));
      const br = built.result || built;
      if (built.error || br.ok === false) { say(`  couldn't build it: ${built.error || br.error}`, ".out-bad"); continue; }
      const kept = await run("song_save", { id }, { silentError: true });
      if (!kept.ok) { say(`  built, but not kept: ${kept.error}`, ".out-bad"); continue; }
      made++;
      say(`  ✓ ${dr.source === "llm" ? "designed by the AI" : "designed offline"}${dr.note ? ` (${dr.note})` : ""}: ${br.summary || "built"}`, ".out-ok");
    }
    say(`Done: ${made} of ${ids.length} song(s) have a new show. Ctrl+Z undoes them one step at a time.`);
    go.disabled = false;
    go.textContent = "Make them again";
    void close;
  });
}
