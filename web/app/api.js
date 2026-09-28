// Talking to the engine: JSON calls, and one live stream for state.
//
// The token (needed once the desk is reachable from other machines) is kept
// for this browser tab only, and asked for at most once at a time - a
// credential that can drive real lights does not belong in long-lived
// storage.

const TOKEN_KEY = "jarvis.token";
let prompting = false;
let snoozeUntil = 0;

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
  const t = window.prompt("This desk needs its access token.\nEnter the CONSOLE_TOKEN from the .env file:");
  prompting = false;
  if (t) { setToken(t.trim()); return true; }
  snoozeUntil = Date.now() + 30000;
  return false;
}

export class ApiError extends Error {}

export async function request(path, body, opts = {}) {
  const init = body === undefined
    ? { headers: headers(false) }
    : { method: "POST", headers: headers(true), body: JSON.stringify(body) };
  if (opts.signal) init.signal = opts.signal;
  const resp = await fetch(path, init);
  if (resp.status === 401) {
    if (askToken()) return request(path, body, opts);
    throw new ApiError("this desk needs its access token");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new ApiError(data.error || `HTTP ${resp.status}`);
  return data;
}

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

  const run = async () => {
    while (!stopped) {
      ctrl = new AbortController();
      try {
        const resp = await fetch("/api/console/stream", { headers: headers(false), signal: ctrl.signal });
        if (resp.status === 401) {
          askToken();
          throw new Error("needs the access token");
        }
        if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
        onStatus(true, null);
        delay = 500;
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
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
        onStatus(false, err.message || String(err));
        await new Promise((r) => setTimeout(r, delay));
        delay = Math.min(delay * 1.8, 5000);
      }
    }
  };
  run();
  return () => { stopped = true; if (ctrl) ctrl.abort(); };
}
