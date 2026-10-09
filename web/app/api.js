// Talking to the engine: JSON calls, and one live stream for state.
//
// The token (needed once the desk is reachable from other machines) is kept
// for this browser tab only, and asked for at most once at a time - a
// credential that can drive real lights does not belong in long-lived
// storage.

const TOKEN_KEY = "jarvis.token";
let prompting = false;
let snoozeUntil = 0;

// a pairing code from the desktop app (Desk -> Other computers, phones and tablets): typed on
// a phone it may come in lower case or with its dash
const clean = (t) => {
  const s = String(t || "").trim();
  const c = s.replace(/[\s-]/g, "");
  return /^[a-z0-9]{8,12}$/i.test(c) ? c.toUpperCase() : s;
};
// ...or in the address the desk shows (?code=...): kept, then taken out of the bar
try {
  const u = new URL(location.href);
  if (u.searchParams.get("code")) {
    sessionStorage.setItem("jarvis.token", clean(u.searchParams.get("code")));
    u.searchParams.delete("code");
    history.replaceState(null, "", u.pathname + u.search + u.hash);
  }
} catch (e) { /* no storage: it will be asked for */ }

export function token() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
}

function setToken(t) {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* storage blocked: we will ask again */ }
}

function headers(json) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  const t = token();
  if (t) h["X-Jarvis-Token"] = t;
  return h;
}

function askToken() {
  if (prompting || Date.now() < snoozeUntil) return false;
  prompting = true;
  const t = window.prompt("This desk needs its code.\nType the pairing code shown on the desk (Desk → Other computers, phones and tablets), or the CONSOLE_TOKEN from .env:");
  prompting = false;
  if (t) { setToken(clean(t)); return true; }
  snoozeUntil = Date.now() + 30000;
  return false;
}

export class ApiError extends Error {}

// Nothing on the screen may wait for ever (a desk step that hangs used to
// leave a button or a dialog "working…" until the page was reloaded).
// Every request gives up in time; the slow ones get longer.
export const DEFAULT_WAIT_MS = 30000;
const SLOW = /^\/api\/(ai|console\/(assistant|ai|autoshow|generate|doctor|scan|rdm|import_show|load|save|models|network|underlay|media_frame|bug_report)|gdtf\/|fixtures\/(from_manual|import|library\/install|real_model))/;
export const waitFor = (path) => (SLOW.test(path) ? 300000 : DEFAULT_WAIT_MS);

export async function request(path, body, opts = {}) {
  const init = body === undefined
    ? { headers: headers(false) }
    : { method: "POST", headers: headers(true), body: JSON.stringify(body) };
  const ms = opts.timeout || waitFor(path);
  const own = new AbortController();
  const timer = setTimeout(() => own.abort(), ms);
  if (opts.signal) {
    if (opts.signal.aborted) own.abort();
    else opts.signal.addEventListener("abort", () => own.abort(), { once: true });
  }
  init.signal = own.signal;
  let resp;
  try {
    resp = await fetch(path, init);
  } catch (e) {
    if (e && e.name === "AbortError") {
      if (opts.signal && opts.signal.aborted) throw e;          // the caller cancelled it
      noteError(`no answer in ${Math.round(ms / 1000)} s: ${path}`);
      throw new ApiError(`The desk didn't answer in ${Math.round(ms / 1000)} seconds (it may still finish). Try again - if it keeps happening, Report a bug.`);
    }
    // the browser's own words for this are "Failed to fetch"
    throw new ApiError("The desk isn't answering - nothing was sent. Reconnecting…");
  } finally {
    clearTimeout(timer);
  }
  if (resp.status === 401) {
    if (askToken()) return request(path, body, opts);
    throw new ApiError("this desk needs its access token");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (resp.status >= 500) noteError(`${path}: ${data.error || "HTTP " + resp.status}`);
    throw new ApiError(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

// Problems the operator should hear about, not find later in a report:
// errors in the page, desk errors, requests that got no answer.  The top
// bar's warning badge (errorbadge.js) shows them.
const problems = [];
const listeners = new Set();
export function noteError(text) {
  problems.push({ t: Date.now(), text: String(text).slice(0, 300) });
  if (problems.length > 30) problems.shift();
  for (const fn of listeners) { try { fn(); } catch (e) { /* a listener's own trouble */ } }
}
export const recentErrors = () => problems.slice();
export function clearErrors() { problems.length = 0; for (const fn of listeners) fn(); }
export function onErrors(fn) { listeners.add(fn); }

export const get = (path) => request(path);
export const post = (path, body = {}) => request(path, body);

/** One engine action. Resolves to the result object ({ok, error, ...}). */
export async function act(action, params = {}) {
  const d = await post("/api/console", { action, params });
  return d.result || {};
}

export async function modelBytes(defId, entry) {
  const url = "/api/console/model?id=" + encodeURIComponent(defId)
    + "&name=" + encodeURIComponent(entry.name);
  const r = await fetch(url, { headers: headers(false) });
  if (!r.ok) throw new Error("model " + r.status);
  return r.arrayBuffer();
}

/**
 * The live stream: `snapshot`, `lite` and `look` events.  Reconnects with
 * backoff; `onStatus(connected, error)` reports the link.
 */
export function openStream(handlers, onStatus) {
  let stopped = false;
  let delay = 500;
  let ctrl = null;
  let lastData = 0;
  let wakeWait = null;           // cuts a reconnect back-off short

  // A computer that slept or changed Wi-Fi keeps a dead connection that
  // never errors.  The server sends something at least every 100 ms
  // (lite), so 3 s of silence means the link is gone: drop it and
  // reconnect (the new connection starts with a full snapshot).
  const watchdog = setInterval(() => {
    if (ctrl && lastData && performance.now() - lastData > 3000) ctrl.abort();
  }, 1000);
  const now = () => {
    if (stopped) return;
    delay = 500;
    if (wakeWait) wakeWait();                       // retry right away
    else if (ctrl && lastData && performance.now() - lastData > 1500) ctrl.abort();
  };
  const onVisible = () => { if (document.visibilityState === "visible") now(); };
  window.addEventListener("online", now);
  document.addEventListener("visibilitychange", onVisible);

  const run = async () => {
    while (!stopped) {
      ctrl = new AbortController();
      lastData = 0;
      try {
        const resp = await fetch("/api/console/stream", { headers: headers(false), signal: ctrl.signal });
        if (resp.status === 401) {
          askToken();
          throw new Error("needs the access token");
        }
        if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
        onStatus(true, null);
        delay = 500;
        lastData = performance.now();
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          lastData = performance.now();
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const block = buf.slice(0, i);
            buf = buf.slice(i + 2);
            let ev = "message", data = "";
            for (const line of block.split("\n")) {
              if (line.startsWith("event: ")) ev = line.slice(7);
              else if (line.startsWith("data: ")) data += line.slice(6);
            }
            if (!data || !handlers[ev]) continue;
            try { handlers[ev](JSON.parse(data)); } catch (e) { console.error(ev, e); }
          }
        }
        throw new Error("stream ended");
      } catch (err) {
        if (stopped) return;
        onStatus(false, err.name === "AbortError" ? "link went quiet" : (err.message || String(err)));
        await new Promise((r) => { wakeWait = r; setTimeout(r, delay); });
        wakeWait = null;
        delay = Math.min(delay * 1.8, 2000);   // the desk back = the screen back, within 2 s
      }
    }
  };
  run();
  return () => {
    stopped = true;
    clearInterval(watchdog);
    window.removeEventListener("online", now);
    document.removeEventListener("visibilitychange", onVisible);
    if (ctrl) ctrl.abort();
  };
}
