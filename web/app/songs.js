// Songs: a light show per song.  The timeline (its music, parts and
// tracks) and the cues it plays are kept under the song's name; playing a
// song puts them back.  With Pro DJ Link a song is tied to a track on the
// CDJs and starts by itself, in step with the deck.
import { state } from "./store.js";
import { run } from "./actions.js";
import { h, modal, promptBox, confirmBox } from "./ui.js";

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
