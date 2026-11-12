// The phone and tablet remote (web/remote.html), in a real browser at the
// sizes of the phones and tablets of the last ten years: it fits (no
// sideways scroll, every button a thumb wide), it plays the desk's buttons,
// the risky ones need a hold, and the bottom strip does what it says.
//
//   node tools/remotecheck.mjs        (CI runs it with the screen check)
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let chromium;
try { ({ chromium } = await import("playwright")); } catch {
  ({ chromium } = await import(process.env.PLAYWRIGHT || "/opt/node22/lib/node_modules/playwright/index.mjs"));
}
const PORT = +(process.env.REMOTE_PORT || 8873), BASE = `http://127.0.0.1:${PORT}/`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (name, ok, extra = "") => { console.log((ok ? "  ok   " : "  FAIL ") + name + (ok ? "" : "  " + String(extra).slice(0, 300))); if (!ok) fails++; };

try { await fetch(BASE); console.log(`something is already on port ${PORT} - stop it or set REMOTE_PORT`); process.exit(2); } catch { /* free */ }
const DATA = mkdtempSync(join(tmpdir(), "haze-remote-"));
const srv = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
  env: { ...process.env, PORT: String(PORT), CONSOLE_DATA_DIR: DATA, CONSOLE_AUTOSAVE: "false", CONSOLE_AUTORESTORE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
const done = (c) => { try { srv.kill(); } catch {} process.exit(c); };
process.on("uncaughtException", (e) => { console.log("  FAIL", e.message.split("\n")[0]); done(1); });
process.on("unhandledRejection", (e) => { console.log("  FAIL", String(e && e.message || e).split("\n")[0]); done(1); });
for (let i = 0; ; i++) { try { if ((await fetch(BASE)).ok) break; } catch {} if (i > 200) { console.log("the desk didn't start"); done(2); } await sleep(250); }
const act = (a, params = {}) => fetch(BASE + "api/console", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: a, params }) }).then((r) => r.json()).then((j) => j.result || j);
const lite = () => fetch(BASE + "api/console?lite=1").then((r) => r.json());

// a show like a real one: headings over blocks, a heading per row, long names, a fader, CO2
await act("venue_template", { name: "club" });
await act("add_heads", { query: "LED PAR", qty: 4 });
const B = (page, slot, button) => act("quick_set", { page, slot, button });
await B(1, 1, { kind: "label", label: "Cobra 120's", w: 3 });
await B(1, 13, { kind: "flash", label: "FLASH", mode: "hold", target: { all: true } });
await B(1, 14, { kind: "colour", label: "Intimidator Wave 360 (L)", mode: "latch", colour: "#ff0000", target: { all: true } });
await B(1, 15, { kind: "sfx", label: "CO2", mode: "tap", target: { type: "co2" } });
await B(1, 25, { kind: "fader", label: "Strobe speed", control: { what: "master" } });
await B(2, 1, { kind: "label", label: "VIP" });
await B(2, 2, { kind: "colour", label: "Blue", mode: "latch", colour: "#0040ff", target: { all: true } });
await act("group_create", { name: "Left Stage", heads: [1, 2] });
await act("group_create", { name: "Right Stage", heads: [3, 4] });
await act("quick_layout", { page: 1, cols: 12, rows: 4 }).catch(() => {});
await act("quick_layout", { page: 2, cols: 12, rows: 4 }).catch(() => {});

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const dev = (w, hgt) => ({ viewport: { width: w, height: hgt }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const SIZES = [["iPhone SE (2016)", dev(320, 568)], ["small Android", dev(360, 640)], ["iPhone 6-8", dev(375, 667)],
  ["iPhone 12-15", dev(390, 844)], ["iPhone Pro Max", dev(430, 932)], ["phone on its side", dev(844, 390)],
  ["iPad", dev(768, 1024)], ["iPad on its side", dev(1180, 820)]];

console.log("Fits every phone and tablet");
for (const [name, d] of SIZES) {
  const ctx = await browser.newContext(d);
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  await p.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
  await p.goto(BASE + "remote.html"); await sleep(1800);
  const m = await p.evaluate(() => {
    const tiles = [...document.querySelectorAll(".rm-btn")].map((e) => e.getBoundingClientRect());
    const strip = document.querySelector("#rm-strip").getBoundingClientRect();
    return { hscroll: document.documentElement.scrollWidth > innerWidth + 1, n: tiles.length,
      minW: Math.min(...tiles.map((r) => r.width)), minH: Math.min(...tiles.map((r) => r.height)),
      // (upright: along the bottom; a phone on its side: down the right edge)
      stripIn: innerWidth > innerHeight && innerHeight <= 520 ? strip.right >= innerWidth - 1 && strip.left > innerWidth * 0.7 && strip.height >= innerHeight - 1
        : strip.bottom <= innerHeight + 1 && strip.top > innerHeight / 2,
      clipped: [...document.querySelectorAll(".rm-btn b")].filter((e) => e.scrollHeight > e.clientHeight + 1).length,
      heads: [...document.querySelectorAll(".rm-head")].map((e) => e.textContent) };
  });
  check(`${name}: no sideways scroll, ${m.n} buttons at least 64 px, names whole, Blackout / Master / Release all at the bottom (on its side: down the right)`,
    !m.hscroll && m.n >= 3 && m.minW >= 64 && m.minH >= 56 && !m.clipped && m.stripIn, JSON.stringify(m));
  check(`${name}: no page errors`, !errs.length, errs.join(" | "));
  await ctx.close();
}

console.log("Plays the desk");
const ctx = await browser.newContext(dev(390, 844));
const p = await ctx.newPage();
const sent = [];
p.on("request", (r) => { if (r.method() === "POST" && /api\/console$/.test(r.url())) { try { sent.push(JSON.parse(r.postData())); } catch { /* fine */ } } });
await p.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
await p.goto(BASE + "remote.html"); await sleep(1800);
check("the block's heading is over its buttons", (await p.locator(".rm-head").first().innerText()).toUpperCase().includes("COBRA"));
const col = p.locator('.rm-btn[data-id="q1-14"]');
await col.dispatchEvent("pointerdown", { pointerId: 5, button: 0, isPrimary: true }); await col.dispatchEvent("pointerup", { pointerId: 5, button: 0 });
await sleep(700);
check("a tap on an on/off button turns it on at the desk", ((await lite()).quick_active || []).includes("q1-14"), JSON.stringify((await lite()).quick_active));
check("...and the tile shows it", await col.evaluate((e) => e.classList.contains("on")));

const co2 = p.locator('.rm-btn[data-id="q1-15"]');
const pressesOf = (id) => sent.filter((x) => x.action === "quick_press" && x.params.id === id && x.params.down).length;
await co2.dispatchEvent("pointerdown", { pointerId: 6, button: 0 }); await sleep(80); await co2.dispatchEvent("pointerup", { pointerId: 6, button: 0 });
await sleep(400);
check("a brush over CO2 doesn't fire it", pressesOf("q1-15") === 0, String(pressesOf("q1-15")));
await co2.dispatchEvent("pointerdown", { pointerId: 7, button: 0 }); await sleep(600); await co2.dispatchEvent("pointerup", { pointerId: 7, button: 0 });
await sleep(300);
check("held a moment, CO2 fires", pressesOf("q1-15") === 1, String(pressesOf("q1-15")));

await p.locator("#rm-bo").click(); await sleep(600);
check("Blackout at the bottom blacks out the desk", (await lite()).blackout === true);
await p.locator("#rm-bo").click(); await sleep(600);
check("...and back", (await lite()).blackout === false);
const rel = p.locator("#rm-rel");
await rel.dispatchEvent("pointerdown", { pointerId: 8, button: 0 }); await sleep(900); await rel.dispatchEvent("pointerup", { pointerId: 8, button: 0 });
await sleep(700);
check("Release all, held, lets go of the buttons", !((await lite()).quick_active || []).includes("q1-14"), JSON.stringify((await lite()).quick_active));
check("no editing on the remote", !(await p.locator("text=/^Edit$/").count()));

// swipe to the next page
await p.evaluate(() => {
  const g = document.querySelector("#rm-grid");
  const ev = (type, x) => g.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: x, clientY: 400, pointerId: 9 }));
  ev("pointerdown", 330); ev("pointerup", 120);
});
await sleep(500);
check("a swipe turns the page", await p.locator('#rm-pages .rm-tab.on[data-p="2"]').count() === 1);
await ctx.close();

console.log("Arranging this phone's buttons");
{
  const ctx3 = await browser.newContext(dev(390, 844));
  const pa = await ctx3.newPage();
  await pa.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
  await pa.goto(BASE + "remote.html"); await sleep(1800);
  const deskBefore = JSON.stringify(((await fetch(BASE + "api/console").then((r) => r.json())).quick || {}).buttons.map((b) => [b.id, b.slot, b.label]));
  const order = () => pa.locator("#rm-grid [data-aid]").evaluateAll((els) => els.map((e) => e.dataset.aid));
  const o0 = await order();
  await pa.locator("#rm-more").click(); await sleep(300);
  await pa.locator(".rm-menu button", { hasText: "Arrange" }).click(); await sleep(500);
  check("⋯ → Arrange shows the bar: drag, size, hide - the desk doesn't change", await pa.locator("#rm-arrange-bar").isVisible());
  const active0 = ((await lite()).quick_active || []).length;
  // drag the long-named colour button up in front of FLASH
  const from = await pa.locator('[data-aid="q1-14"]').boundingBox(), to = await pa.locator('[data-aid="q1-13"]').boundingBox();
  await pa.mouse.move(from.x + from.width / 2, from.y + 20); await pa.mouse.down();
  for (let i = 1; i <= 10; i++) { await pa.mouse.move(from.x + from.width / 2 + (to.x + 10 - from.x - from.width / 2) * i / 10, from.y + 20 + (to.y + 20 - from.y - 20) * i / 10); await sleep(30); }
  const mid = await pa.evaluate(() => ({ ghost: document.querySelectorAll(".rm-ghost").length, slot: !!document.querySelector(".rm-slot") }));
  check("dragging: a lifted copy follows the finger, a dashed slot shows where it lands", mid.ghost === 1 && mid.slot, JSON.stringify(mid));
  await sleep(250);                                  // (held still: the slot settles there, the others slide over)
  const slid = await pa.evaluate(() => [...document.querySelectorAll("#rm-grid [data-aid]")].some((e) => /transform/.test(e.style.transition)));
  check("...the other tiles slide out of the way (not jump)", slid);
  await pa.mouse.up(); await sleep(500);
  check("...let go, the copy glides in and is gone", await pa.evaluate(() => !document.querySelector(".rm-ghost") && !document.querySelector(".rm-slot")));
  const o1 = await order();
  check("a tile dragged in front of another moves there", o1.indexOf("q1-14") < o1.indexOf("q1-13") && o0.indexOf("q1-14") > o0.indexOf("q1-13"), `${o0} -> ${o1}`);
  check("...and arranging pressed nothing", ((await lite()).quick_active || []).length === active0);
  // size and hide
  await pa.locator('[data-aid="q1-13"] .rm-chip', { hasText: "⤢" }).click(); await sleep(400);
  check("⤢ makes a tile bigger on this phone", /wide/.test(await pa.locator('[data-aid="q1-13"] .rm-chip').first().innerText()));
  await pa.locator('[data-aid="q1-15"] .rm-chip', { hasText: "hide" }).click(); await sleep(400);
  await pa.locator("#rm-arrange-done").click(); await sleep(500);
  check("a hidden tile is gone once arranged", await pa.locator('[data-aid="q1-15"]').count() === 0);
  await pa.reload(); await sleep(1800);
  const o2 = await order();
  check("the phone keeps its layout after a reload", o2.indexOf("q1-14") < o2.indexOf("q1-13") && !o2.includes("q1-15"), o2.join(","));
  const deskAfter = JSON.stringify(((await fetch(BASE + "api/console").then((r) => r.json())).quick || {}).buttons.map((b) => [b.id, b.slot, b.label]));
  check("the desk's buttons didn't change", deskBefore === deskAfter);
  await pa.locator("#rm-more").click(); await sleep(300);
  await pa.locator(".rm-menu button", { hasText: "Arrange" }).click(); await sleep(400);
  await pa.locator("#rm-arrange-reset").click(); await sleep(400);
  await pa.locator("#rm-arrange-done").click(); await sleep(400);
  check("Desk's layout puts the page back as the desk has it", JSON.stringify(await order()) === JSON.stringify(o0), (await order()).join(","));
  await ctx3.close();
}

console.log("Faders");
{
  const ctx2 = await browser.newContext(dev(375, 667));
  const pf = await ctx2.newPage();
  await pf.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
  await pf.goto(BASE + "remote.html"); await sleep(1800);
  const tab = pf.locator("#rm-views .rm-view", { hasText: "Faders" });
  check("a Faders tab when the show has groups", await tab.count() === 1);
  await tab.click(); await sleep(500);
  const names = await pf.locator(".rm-fader b").allInnerTexts();
  check("the grand master and a fader per group", names.join("|") === "Grand master|Left Stage|Right Stage", names.join("|"));
  const g = (await lite()).group_masters || {};
  const right = pf.locator('.rm-fader[data-key^="g"] input').nth(1);
  await right.evaluate((el) => { el.value = 40; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); });
  await sleep(700);
  const g2 = (await lite()).group_masters || {};
  check("moving a group's fader sets that group's master at the desk", Object.values(g2).includes(40) && JSON.stringify(g) !== JSON.stringify(g2), JSON.stringify(g2));
  check("no sideways scroll on the Faders tab", !(await pf.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)));
  // hidden on this phone from the ⋯ menu, and still hidden after a reload
  await pf.locator("#rm-more").click(); await sleep(300);
  await pf.locator(".rm-menu .rm-tabtoggle", { hasText: "Faders" }).click(); await sleep(400);
  const before = ((await lite()).quick_active || []).length;
  // (a tile clear of the menu, which opens at the top right)
  const tileUnder = await pf.locator("#rm-grid .rm-btn").first().boundingBox();
  await pf.mouse.click(tileUnder.x + tileUnder.width / 2, tileUnder.y + tileUnder.height / 2); await sleep(500);
  check("a tap outside the ⋯ menu closes it without pressing the button under it",
    await pf.locator(".rm-menu").count() === 0 && ((await lite()).quick_active || []).length === before);
  check("⋯ → Faders tab hides it on this phone (back to Buttons)", await pf.locator("#rm-views .rm-view", { hasText: "Faders" }).count() === 0
    && await pf.locator("#rm-grid .rm-btn").count() > 0);
  await pf.reload(); await sleep(1800);
  check("...and it stays hidden after a reload", await pf.locator("#rm-views .rm-view", { hasText: "Faders" }).count() === 0);
  await pf.locator("#rm-more").click(); await sleep(300);
  await pf.locator(".rm-menu .rm-tabtoggle", { hasText: "Faders" }).click(); await sleep(400);
  check("...and comes back when shown again", await pf.locator("#rm-views .rm-view", { hasText: "Faders" }).count() === 1);
  await ctx2.close();
}

console.log("Group colours: the desk and the phone match");
{
  const desk = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await desk.addInitScript(() => { try { sessionStorage.setItem("haze.venuepick", "1"); localStorage.setItem("haze.noLockAsk", "1"); } catch (e) { /* fine */ } });
  await desk.goto(BASE); await sleep(3000);
  const dots = desk.locator("#grp-masters .m-colour");
  check("each group fader on the desk has its colour dot", await dots.count() === 2, String(await dots.count()));
  await dots.nth(1).click(); await sleep(300);
  await desk.locator(".menu button", { hasText: "Red" }).click(); await sleep(800);
  const snapG = await fetch(BASE + "api/console").then((r) => r.json()).then((j) => (j.groups || []).find((g) => g.name === "Right Stage"));
  check("picking Red saves it with the group", snapG && snapG.colour === "#ef4444", JSON.stringify(snapG));
  const fill = await desk.locator("#grp-masters .master.grp").nth(1).evaluate((el) => getComputedStyle(el).getPropertyValue("--accent").trim());
  check("...and the desk's fader turns red", fill === "#ef4444", fill);
  const ph2 = await (await browser.newContext(dev(390, 844))).newPage();
  await ph2.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
  await ph2.goto(BASE + "remote.html"); await sleep(1500);
  await ph2.locator("#rm-views .rm-view", { hasText: "Faders" }).click(); await sleep(500);
  const tint = await ph2.locator('.rm-fader').nth(2).evaluate((el) => el.style.getPropertyValue("--tint"));
  check("the phone's Right Stage fader is red too", tint.replace(/\s/g, "") === "rgb(239,68,68)", tint);
}

console.log("A phone opening the desk");
const ph = await browser.newContext(dev(390, 844));
const pp = await ph.newPage();
await pp.goto(BASE); await sleep(1500);
check("gets the remote", /remote\.html/.test(pp.url()), pp.url());
await pp.goto(BASE + "?desk=1"); await sleep(1500);
check("...unless it asks for the full desk", !/remote\.html/.test(pp.url()), pp.url());
await ph.close();

console.log("The desk's phone button");
{
  const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const d = await dctx.newPage();
  const derrs = [];
  d.on("pageerror", (e) => derrs.push(e.message));
  await d.goto(BASE); await sleep(3500);
  await d.click("#phone-btn"); await sleep(1500);
  const m = await d.evaluate(() => {
    const box = document.querySelector(".phone-qr");
    return { open: !!box, title: [...document.querySelectorAll(".modal-head h2")].map((x) => x.textContent), text: box ? box.textContent : "" };
  });
  // (this desk answers only on this computer: the window says so, and how to let phones in)
  check("the top bar's phone button opens the phone remote's QR window (or says how to let phones in)",
    m.open && m.title.includes("Phone remote") && /(Scan with the phone|aren't let in yet)/.test(m.text), JSON.stringify(m));
  check("...no page errors", !derrs.length, derrs.join(" | "));
  await dctx.close();
}

await browser.close();

// One home-screen icon for every venue: a desk letting phones in answers to
// haze.local too; the remote, opened at the address, moves to the name (its
// layout and switches with it).  The browser here is told haze.local is this
// computer - as a phone that can look names up finds it.
console.log("One icon for every venue (the desk's name, haze.local)");
{
  const NP = PORT + 1, CODE = "ABCDE12345", NB = `http://127.0.0.1:${NP}/`;
  const DATA2 = mkdtempSync(join(tmpdir(), "haze-remote-name-"));
  const srv2 = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "app.main"], { stdio: "ignore",
    env: { ...process.env, HOST: "0.0.0.0", CONSOLE_TOKEN: CODE, PORT: String(NP), CONSOLE_DATA_DIR: DATA2, CONSOLE_AUTOSAVE: "false", CONSOLE_AUTORESTORE: "false", DMX_HOST: "127.0.0.1", AUTO_UPDATE: "false" } });
  try {
    for (let i = 0; ; i++) { try { await fetch(NB); break; } catch {} if (i > 200) throw new Error("the second desk didn't start"); await sleep(250); }
    const info = await fetch(NB + "api/remote", { headers: { "X-Haze-Token": CODE } }).then((r) => r.json());
    check("a desk letting phones in has a name on the network", /^haze(-\d)?\.local$/.test(info.name || ""), JSON.stringify(info).slice(0, 200));
    await fetch(NB + "api/console", { method: "POST", headers: { "content-type": "application/json", "X-Haze-Token": CODE },
      body: JSON.stringify({ action: "venue_template", params: { name: "club" } }) });
    const club = await fetch(NB + "remote/manifest.webmanifest").then((r) => r.json());
    await fetch(NB + "api/console", { method: "POST", headers: { "content-type": "application/json", "X-Haze-Token": CODE },
      body: JSON.stringify({ action: "venue_save", params: { name: "Zyped Club" } }) });
    const named2 = await fetch(NB + "remote/manifest.webmanifest").then((r) => r.json());
    const page = await fetch(NB + "remote.html").then((r) => r.text());
    check("the home-screen icon is named after the venue (a template's stock name doesn't count)",
      club.short_name === "Haze" && named2.short_name === "Zyped Club" && /apple-mobile-web-app-title" content="Zyped Club"/.test(page),
      JSON.stringify([club.short_name, named2.short_name]));
    const hello = await fetch(NB + "hello.json").then((r) => r.json());
    check("...and says which desk it is to anyone who asks (not the code)", hello.haze === true && hello.id === info.id && !JSON.stringify(hello).includes(CODE), JSON.stringify(hello));
    const named = await chromium.launch({ ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}), args: [`--host-resolver-rules=MAP ${info.name} 127.0.0.1`] });
    const nctx = await named.newContext(dev(390, 844));
    const np = await nctx.newPage();
    await np.addInitScript(() => { try { if (location.hostname === "127.0.0.1") { localStorage.setItem("haze.remote.tip", "1"); localStorage.setItem("haze.remote.hidden", '["timeline"]'); } } catch (e) { /* fine */ } });
    await np.goto(NB + "remote.html?code=" + CODE);
    for (let i = 0; i < 40 && new URL(np.url()).hostname !== info.name; i++) await sleep(250);
    await sleep(1500);
    const at = await np.evaluate(() => ({ host: location.hostname, url: location.href, connect: !document.querySelector("#rm-connect").hidden,
      dot: document.querySelector("#rm-dot").classList.contains("on"), carried: localStorage.getItem("haze.remote.hidden") }));
    check("the remote opened at the address moves to the desk's name, signed in (no code asked)", at.host === info.name && !at.connect && at.dot && !/code=|carry=/.test(at.url), JSON.stringify(at));
    check("...and this phone's own settings come with it", at.carried === '["timeline"]', at.carried);
    await named.close();
    // a phone that can't look names up: it stays at the address, and says so once
    const plain = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
    const pp = await (await plain.newContext(dev(390, 844))).newPage();
    await pp.addInitScript(() => { try { localStorage.setItem("haze.remote.tip", "1"); } catch (e) { /* fine */ } });
    await pp.goto(NB + "remote.html?code=" + CODE);
    let st = {};
    for (let i = 0; i < 40; i++) {
      st = await pp.evaluate(() => ({ host: location.hostname, text: document.body.innerText }));
      if (/can't find the desk by its name/.test(st.text)) break;
      await sleep(200);
    }
    check("...a phone that can't find the name stays at the address and says so", st.host === "127.0.0.1" && /can't find the desk by its name/.test(st.text), st.host + " " + st.text.slice(0, 200));
    await plain.close();
  } finally { try { srv2.kill(); } catch {} }
}
console.log(fails ? `${fails} failed` : "all ok");
done(fails ? 1 : 0);
