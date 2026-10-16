// Songs from a rekordbox USB stick: pick the stick (or its PIONEER folder),
// see its analysed tracks, add the ones you want as songs - each with the
// parts rekordbox found (intro, build, drop, breakdown, outro...) and its
// beat grid, no music file needed.  Only three small sections of each
// track's analysis files are sent to the desk (app/rekordbox.py reads them).
import { h, modal, toast } from "./ui.js";
import { post } from "./api.js";
import { pickTags, pathOf } from "./anlz.js";

const b64 = (u8) => {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000));
  return btoa(s);
};
const nameOf = (path) => (path.replace(/\\/g, "/").split("/").pop() || "").replace(/\.[A-Za-z0-9]{2,4}$/, "");

/** The analysed tracks in a picked folder: [{dir, dat: File, ext: File}]. */
function tracksIn(files) {
  const by = new Map();
  for (const f of files) {
    const m = /^(.*)\/ANLZ\d+\.(DAT|EXT)$/i.exec(f.webkitRelativePath || f.name);
    if (!m) continue;
    const t = by.get(m[1]) || { dir: m[1] };
    t[m[2].toLowerCase()] = f;
    by.set(m[1], t);
  }
  return [...by.values()].filter((t) => t.dat && t.ext);
}

export function openRekordboxImport(done) {
  const pick = h("input", { type: "file", multiple: true, style: { display: "none" } });
  pick.webkitdirectory = true;                 // a whole folder (the stick)
  const list = h("div.song-picks");
  const status = h("p.muted.small", "");
  const add = h("button.btn.primary", { disabled: true }, "Add as songs");
  let found = [];
  const body = h("div.songs-box",
    h("p.muted.small", "Pick the DJ's USB stick (or its PIONEER folder). Every track rekordbox analysed with phrases on "
      + "becomes a song with its parts - intro, build, drop, breakdown, outro - and its tempo, so Make shows for songs can "
      + "design its show without the music file. Then play the track on the master CDJ and press Tie to deck."),
    h("div.row-btns", h("button.btn", { onclick: () => pick.click() }, "Pick the USB stick…"), add),
    status, list, pick);
  const close = modal({ title: "Songs from a rekordbox USB", wide: true, body });
  pick.addEventListener("change", async () => {
    found = tracksIn([...pick.files]);
    if (!found.length) {
      status.textContent = "No rekordbox analysis here: pick the stick itself or its PIONEER folder (PIONEER/USBANLZ holds the analysis).";
      return;
    }
    status.textContent = `Reading ${found.length} track(s)…`;
    for (const t of found) {
      try { t.name = nameOf(pathOf(pickTags(await t.dat.arrayBuffer(), ["PPTH"]).PPTH)) || t.dir.split("/").pop(); } catch { t.name = t.dir; }
    }
    found.sort((a, b) => a.name.localeCompare(b.name));
    const shown = found.slice(0, 400);
    list.replaceChildren(
      h("label.check", h("input", { type: "checkbox", onchange: (e) => list.querySelectorAll("input[data-i]").forEach((c) => { c.checked = e.target.checked; }) }), h("b", "All")),
      ...shown.map((t, i) => h("label.check", h("input", { type: "checkbox", "data-i": i }), h("span", t.name))));
    status.textContent = `${found.length} analysed track(s)` + (found.length > shown.length ? ` (the first ${shown.length} shown)` : "") + ": tick the ones to add.";
    add.disabled = false;
  });
  add.addEventListener("click", async () => {
    const chosen = [...list.querySelectorAll("input[data-i]:checked")].map((c) => found[+c.dataset.i]);
    if (!chosen.length) { toast("Tick a track"); return; }
    add.disabled = true;
    let added = 0;
    const failed = [];
    for (let i = 0; i < chosen.length; i += 20) {
      status.textContent = `Adding ${Math.min(i + 20, chosen.length)} of ${chosen.length}…`;
      const batch = [];
      for (const t of chosen.slice(i, i + 20)) {
        const dat = pickTags(await t.dat.arrayBuffer(), ["PPTH", "PQTZ"]);
        const ext = pickTags(await t.ext.arrayBuffer(), ["PSSI"]);
        const tags = {};
        for (const [k, u8] of Object.entries({ ...dat, ...ext })) tags[k] = b64(u8);
        batch.push({ name: t.name, tags });
      }
      const d = await post("/api/console/rekordbox", { tracks: batch }).catch((e) => ({ error: e.message }));
      const r = d.result || d;
      failed.push(...(r.failed || []));
      if (r.ok === false && !(r.failed || []).length) failed.push({ name: "", error: d.error || r.error });
      added += (r.added || []).length + (r.updated || []).length;
    }
    status.textContent = `${added} song(s) added.` + (failed.length ? ` ${failed.length} not: ${failed.slice(0, 3).map((f) => `${f.name} (${f.error})`).join("; ")}` : "");
    toast(`${added} song(s) from rekordbox - next: Make shows for songs`, added ? "ok" : "bad", 6000);
    add.disabled = false;
    if (added && done) { close(); done(); }
  });
}
