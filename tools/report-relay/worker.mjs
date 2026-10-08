// The report relay (bug reports without a GitHub account).  A Cloudflare
// Worker: the desk POSTs a saved report here; the relay files the issue on
// the desk's GitHub page with its own token and keeps the zip (in an R2
// bucket, downloadable only with the maintainers' key).  Set up: README.md.
//
// Secrets / settings (wrangler secret put ... / [vars]):
//   GITHUB_TOKEN   a fine-grained token: this repository only, Issues: read and write
//   REPO           "owner/name" of the desk's repository
//   DOWNLOAD_KEY   what a maintainer adds (?key=) to download a report's zip
//   RELAY_KEY      optional: desks must send it (REPORT_RELAY_KEY in their .env)
//   REPORTS        an R2 bucket binding for the zips (optional: without it,
//                  the issue is filed and says the zip stayed on the desk)

const MAX_BODY = 12 * 1024 * 1024;              // the desk sends at most 8 MB of zip (base64: ~11 MB)
const PER_HOUR = 6;                             // reports from one address
const TEMPLATES = { "bug.yml": "bug", "light-bug.yml": "light-bug", "fixture-request.yml": "fixture-request" };
const FIELDS = [["light", "Which light?"], ["mode", "DMX mode"], ["where", "Patch"], ["area", "Where does it go wrong?"],
  ["what", "What happened, and what should have happened?"], ["manual", "Manual / DMX chart"], ["files", "Files"],
  ["version", "Desk version / computer"]];
const LABEL = /^(bug|light-bug|fixture-request|(brand|model):[^,\n]{1,60})$/;
const ZIP_NAME = /^jarvis-report-[\w-]{1,120}\.zip$/;
const recent = new Map();                       // address -> times (best effort, per worker instance)

const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
// no @mentions or links pinging people from a report's text
const tame = (text, max) => String(text ?? "").slice(0, max).replace(/@/g, "@​");

function allowed(addr, now = Date.now()) {
  const times = (recent.get(addr) || []).filter((t) => now - t < 3600e3);
  if (times.length >= PER_HOUR) return false;
  times.push(now);
  recent.set(addr, times);
  if (recent.size > 5000) recent.clear();
  return true;
}

function issueBody(fields, link, zipName) {
  const parts = [];
  for (const [key, label] of FIELDS) {
    if (key === "files") continue;
    const v = fields && fields[key];
    if (v) parts.push(`### ${label}\n\n${tame(v, 4000)}`);
  }
  parts.push("### Report\n\n" + (link
    ? `[${zipName}](${link}) - sent from the desk (maintainers: add \`?key=\` with the relay's download key)`
    : `${zipName || "(no file)"} - sent from the desk; the relay keeps no files, ask the reporter for it`));
  parts.push("_Filed by the report relay for someone without a GitHub account._");
  return parts.join("\n\n");
}

async function file(env, title, body, labels) {
  const post = (lbls) => fetch(`https://api.github.com/repos/${env.REPO}/issues`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
      "User-Agent": "jarvis-report-relay", "Content-Type": "application/json" },
    body: JSON.stringify({ title, body, ...(lbls.length ? { labels: lbls } : {}) }),
  });
  let r = await post(labels);
  if (r.status === 422 && labels.length) r = await post([]);       // a label it may not set: file it without
  return r;
}

async function report(req, env, url) {
  if (env.RELAY_KEY && req.headers.get("X-Relay-Key") !== env.RELAY_KEY) return json({ error: "wrong relay key" }, 403);
  if ((+req.headers.get("Content-Length") || 0) > MAX_BODY) return json({ error: "the report is too big" }, 413);
  if (!allowed(req.headers.get("CF-Connecting-IP") || "?")) return json({ error: "too many reports from here - try again in an hour" }, 429);
  const text = await req.text();
  if (text.length > MAX_BODY) return json({ error: "the report is too big" }, 413);
  let b;
  try { b = JSON.parse(text); } catch { return json({ error: "that isn't a report" }, 400); }
  const kind = TEMPLATES[b && b.template];
  const title = tame(b && b.title, 200).trim();
  if (!kind || !title) return json({ error: "that isn't a report" }, 400);
  const labels = [...new Set([kind, ...(Array.isArray(b.labels) ? b.labels : [])].map(String).filter((l) => LABEL.test(l)))].slice(0, 6);
  let link = "";
  const zipName = ZIP_NAME.test(String(b.zip_name || "")) ? b.zip_name : "";
  if (b.zip && zipName && env.REPORTS) {
    let bytes;
    try { bytes = Uint8Array.from(atob(String(b.zip)), (c) => c.charCodeAt(0)); } catch { return json({ error: "the report's file is damaged" }, 400); }
    if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) return json({ error: "the report's file isn't a zip" }, 400);
    const id = crypto.randomUUID();
    await env.REPORTS.put(`${id}.zip`, bytes, { customMetadata: { name: zipName } });
    link = `${url.origin}/r/${id}`;
  }
  const r = await file(env, title, issueBody(b.fields, link, zipName), labels);
  if (!r.ok) return json({ error: `GitHub answered ${r.status}` }, 502);
  const issue = await r.json();
  return json({ issue_url: issue.html_url, number: issue.number });
}

async function download(env, url) {
  const id = url.pathname.slice(3);
  if (!env.DOWNLOAD_KEY || url.searchParams.get("key") !== env.DOWNLOAD_KEY) return json({ error: "a maintainer's download key is needed" }, 403);
  if (!/^[0-9a-f-]{36}$/.test(id) || !env.REPORTS) return json({ error: "no such report" }, 404);
  const obj = await env.REPORTS.get(`${id}.zip`);
  if (!obj) return json({ error: "no such report" }, 404);
  const name = (obj.customMetadata && obj.customMetadata.name) || `${id}.zip`;
  return new Response(obj.body, { headers: { "Content-Type": "application/zip", "Content-Disposition": `attachment; filename="${name}"` } });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === "/") return report(req, env, url);
    if (req.method === "GET" && url.pathname.startsWith("/r/")) return download(env, url);
    if (req.method === "GET" && url.pathname === "/") return json({ ok: true, relay: "jarvis reports" });
    return json({ error: "not here" }, 404);
  },
};
