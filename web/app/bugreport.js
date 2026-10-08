// Report a problem, with the evidence attached (backlog A11): the light's
// file, its DMX, what the 3D is told, a picture of the 3D, the desk version
// and recent errors, and (ticked) the whole show.  The reporter sees the
// list before anything is saved; the zip is saved and a pre-filled GitHub
// issue opens, to drag it into.  Never: .env, the AI key or passwords.
import { request, token, recentErrors } from "./api.js";
import { head as headOf } from "./store.js";
import { h, modal, toast } from "./ui.js";

// the page's own recent errors, for the report
const pageErrors = [];
const keep = (msg) => { pageErrors.push(String(msg).slice(0, 300)); if (pageErrors.length > 30) pageErrors.shift(); };
window.addEventListener("error", (e) => keep(e.message || e));
window.addEventListener("unhandledrejection", (e) => keep((e.reason && e.reason.message) || e.reason));

const AREAS = [["dmx", "The real light (DMX)"], ["3d", "The 3D view"], ["programmer", "The programmer"],
  ["effects", "Effects / lasers / fog"], ["cues", "Cues or playbacks"], ["crash", "The desk crashed or froze"]];
// a report that doesn't come back in time says so, instead of "Saving…" for ever
const WAIT_MS = 25000;
const SEND_WAIT_MS = 70000;          // saving, then sending it to the report relay
async function ask(body) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), body.send ? SEND_WAIT_MS : WAIT_MS);
  try {
    return await request("/api/console/bug_report", body, { signal: ctl.signal });
  } catch (e) {
    return { error: e && e.name === "AbortError"
      ? "The desk didn't answer in 25 seconds. Untick \"Attach the whole show\" and try again - or restart the desk."
      : (e && e.message) || "The desk didn't answer" };
  } finally {
    clearTimeout(timer);
  }
}
const kb = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);

/** `head` = a light's number, or nothing for "something else is wrong". */
export function openBugReport(head = null) {
  const hd = head ? headOf(head) : null;
  const what = h("textarea", { rows: 4, placeholder: hd
    ? "What's wrong, and what should happen?  e.g. I press Full and the real light stays dark."
    : "What happened, and what should have happened?" });
  const ticks = AREAS.map(([id, label]) => ({ id, box: h("input", { type: "checkbox" }), label }));
  const show = h("input", { type: "checkbox", checked: true });
  const list = h("ul.report-files");
  const sending = h("div.muted.small");
  const payload = (extra = {}) => ({
    head, what: what.value.trim(), areas: ticks.filter((t) => t.box.checked).map((t) => t.id),
    include_show: show.checked,
    // the page's own errors, plus what the warning badge saw (no answer, desk errors)
    page_errors: [...pageErrors, ...recentErrors().map((x) => `${new Date(x.t).toLocaleTimeString()} ${x.text}`)].slice(-30), ...extra });
  const preview = async () => {
    list.replaceChildren(h("li.muted", "Gathering…"));
    const d = await ask(payload({ preview: true }));
    if (d.error) { list.replaceChildren(h("li", d.error)); return; }
    relay = !!d.relay;
    sendBtn.hidden = !relay;
    saveBtn.className = relay ? "btn" : "btn primary";
    note.textContent = relay
      ? "Never included: the .env file, the AI key or any password. Send report files it for you - no GitHub account needed; "
        + "or save it and open GitHub yourself."
      : "Never included: the .env file, the AI key or any password. "
        + "It is saved on this computer; you drag it into the GitHub issue that opens (a GitHub account is needed).";
    list.replaceChildren(...d.files.map((f) => h("li", h("code", f.name), h("span.muted", " " + kb(f.bytes)))),
      h("li", h("code", "3d-view.png"), h("span.muted", " a picture of the 3D view")));
  };
  show.addEventListener("change", preview);
  let busy = false;
  let relay = false;
  const send = async (viaRelay = false) => {
    if (!what.value.trim()) { toast("Say what's wrong first", "bad"); what.focus(); return; }
    if (busy) return;
    busy = true;
    saveBtn.disabled = sendBtn.disabled = true;
    sending.textContent = viaRelay ? "Sending the report…" : "Saving the report…";
    let picture = "";
    try { picture = window.jarvisStage ? window.jarvisStage.photo(1600) : ""; } catch { /* no 3D in this window */ }
    const d = await ask(payload({ picture, send: viaRelay }));
    busy = false;
    saveBtn.disabled = sendBtn.disabled = false;
    if (d.error) { sending.textContent = d.error; return; }
    if (viaRelay && d.sent) {
      toast(`Report sent - issue #${d.sent.number || "?"}. Thank you!`, "ok", 9000);
      window.open(d.sent.issue_url, "_blank", "noopener");
      close();
      return;
    }
    if (viaRelay) {
      // not sent: say why, and leave the GitHub way open (the report is saved)
      sending.textContent = `${d.send_error || "It couldn't be sent."} It is saved as ${d.zip}.`;
      return;
    }
    // the zip, saved where downloads go (fetched with the desk's key, then handed over)
    const t = token();
    const r = await fetch("/api/console/bug_report?name=" + encodeURIComponent(d.zip), { headers: t ? { "X-Jarvis-Token": t } : {} });
    if (r.ok) {
      const a = h("a", { href: URL.createObjectURL(await r.blob()), download: d.zip });
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    }
    window.open(d.url, "_blank", "noopener");
    toast(`Report saved as ${d.zip}. Drag it into the GitHub page that opened, then Submit.`, "ok", 9000);
    close();
  };
  const saveBtn = h("button.btn.primary", { onclick: () => send(false) }, "Save report and open GitHub");
  const sendBtn = h("button.btn.primary", { hidden: true, onclick: () => send(true),
    title: "Files the report on the desk's GitHub page for you, with everything attached - no GitHub account needed" }, "Send report");
  const note = h("p.muted.small", "Never included: the .env file, the AI key or any password. "
    + "It is saved on this computer; you drag it into the GitHub issue that opens (a GitHub account is needed).");
  const close = modal({
    title: hd ? `Report a problem with #${head} ${hd.model || hd.name || ""}` : "Report a bug",
    body: h("div.report-form",
      h("label.field", h("span", "What's wrong?"), what),
      hd ? h("div.field", h("span", "Where does it go wrong?"),
        h("div.report-ticks", ...ticks.map((t) => h("label.check", t.box, h("span", t.label))))) : null,
      h("label.check", show, h("span", "Attach the whole show, so the exact rig can be loaded for testing")),
      h("div.field", h("span", "What goes in the report"), list, note),
      sending),
    foot: [h("button.btn", { onclick: () => close() }, "Cancel"),
      saveBtn, sendBtn],
  });
  preview();
  setTimeout(() => what.focus(), 50);
}
