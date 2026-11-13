// Settings -> Phone & tablet: the remote's address and a QR code to scan.
// The code in the QR is the desk's pairing code, so the phone signs in by
// itself - anyone who scans it can play the show, so it shows only here.

import { get } from "./api.js";
import { h } from "./ui.js";

let loading = null;
// the QR maker (web/vendor/qrcode, MIT): a plain script, loaded on first use
function qrLib() {
  if (window.qrcode) return Promise.resolve(window.qrcode);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/vendor/qrcode/qrcode.js";
      s.onload = () => resolve(window.qrcode);
      s.onerror = () => { loading = null; reject(new Error("the QR maker didn't load")); };
      document.head.append(s);
    });
  }
  return loading;
}

/** A QR code for `text`, as an <svg> element (black on white: what phones read best). */
export async function qrSvg(text, cell = 5) {
  const qrcode = await qrLib();
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount(), m = 4, size = (n + m * 2) * cell;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${n + m * 2} ${n + m * 2}`);
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "QR code for the phone remote");
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + m} ${r + m}h1v1h-1z`;
  const bg = document.createElementNS(NS, "rect");
  bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", "#fff");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d); path.setAttribute("fill", "#000"); path.setAttribute("shape-rendering", "crispEdges");
  svg.append(bg, path);
  return svg;
}

const showCode = (c) => (c && c.length === 10 ? `${c.slice(0, 5)}-${c.slice(5)}` : c);

/** The Settings section: on, the address and a QR per network; off, how to turn it on. */
export function remotePanel() {
  const box = h("div.remote-box", h("p.muted.small", "Looking up this computer's address…"));
  get("/api/remote").then(async (r) => {
    if (!r.network) {
      box.replaceChildren(
        h("p", "Play the show's buttons, cues and timeline from a phone or tablet: big buttons, Blackout and the master always at the bottom, nothing that edits the show."),
        h("p.muted.small", window.hazeDesk
          ? "First let phones in: Desk → Other computers, phones and tablets… (the desk restarts once). Then come back here for the QR code."
          : "This desk only answers on this computer. Start it with HOST=0.0.0.0 and a CONSOLE_TOKEN in .env to let phones in."));
      return;
    }
    const all = r.addresses.length ? r.addresses : [{ address: location.hostname, tailscale: false }];
    // a VPN's own address (Radmin VPN 26.x, Hamachi 25.x...) is no use to a
    // phone on the Wi-Fi: only shown when there's nothing else, and said so
    const addrs = all.some((a) => !a.vpn) ? all.filter((a) => !a.vpn) : all;
    const vpns = all.filter((a) => a.vpn && !addrs.includes(a));
    const cards = await Promise.all(addrs.slice(0, 3).map(async (a) => {
      const url = `http://${a.address}:${r.port}/remote.html`;
      const svg = await qrSvg(`${url}?code=${encodeURIComponent(r.code)}`).catch(() => null);
      return h("div.remote-card",
        svg || h("p.muted.small", "(no QR code - type the address)"),
        h("div",
          h("b", a.tailscale ? "From anywhere (Tailscale)" : a.vpn ? "Only over a VPN (not a phone on the Wi-Fi)" : "On this Wi-Fi"),
          h("code.remote-url", url),
          h("p.muted.small", "Scan with the phone's camera: it opens signed in.")));
    }));
    box.replaceChildren(
      h("p", "Scan with a phone or tablet on the same Wi-Fi. It opens the remote: your button pages, Cues and Timeline when the show has them, and Blackout, the master and Release all along the bottom. It can't edit the show."),
      ...cards,
      vpns.length ? h("p.muted.small", `Not for phones: ${vpns.map((a) => a.address).join(", ")} - a VPN on this computer (Radmin VPN, Hamachi...). A phone on the Wi-Fi can't reach it and just keeps loading.`) : null,
      h("p.muted.small", `Pairing code: ${showCode(r.code)} (typed on the phone if it can't scan). Anyone with the code can play the desk - "New code" in Desk → Other computers, phones and tablets signs every phone out.`),
      h("h3", "Full screen, like an app"),
      h("ul.remote-tips",
        h("li", h("b", "iPhone / iPad: "), "in Safari tap Share → Add to Home Screen. The Haze icon opens the remote full screen, no browser bars."),
        h("li", h("b", "Android: "), "in Chrome ⋮ → Add to Home screen, or tap ⋯ → Full screen in the remote."),
        h("li", "The screen stays on while the remote is open (after the first tap). CO2, confetti, sparks, flames and lasers play as on the desk: they fire only while the desk is armed (ARM FX).")));
  }).catch((e) => box.replaceChildren(h("p.muted.small", `Couldn't get the address: ${e.message}`)));
  return box;
}

/** The phone remote's QR code, big, in its own window (the top bar's phone
 *  button; the desktop app's Desk -> Other computers, phones and tablets):
 *  a phone on the same Wi-Fi scans it and opens the remote signed in -
 *  no address to type, no code. */
export async function openPhoneQr() {
  const { modal } = await import("./ui.js");
  const body = h("div.phone-qr", h("p.muted.small", "Looking up this computer's address…"));
  const close = modal({ title: "Phone remote", body, foot: [h("button.btn.primary", { onclick: () => close() }, "Done")] });
  let r;
  try { r = await get("/api/remote"); } catch (e) { body.replaceChildren(h("p", `Couldn't get the address: ${e.message}`)); return; }
  if (!r.network) {
    body.replaceChildren(
      h("p", "Phones aren't let in yet: this desk only answers on this computer."),
      window.hazeDesk && window.hazeDesk.win
        ? h("button.btn.primary", { onclick: () => { close(); window.hazeDesk.win.pick("phones"); } }, "Allow phones and tablets…")
        : h("p.muted.small", "Start the desk with HOST=0.0.0.0 and a CONSOLE_TOKEN in .env to let phones in."));
    return;
  }
  const all = r.addresses.length ? r.addresses : [{ address: location.hostname, tailscale: false }];
  const usable = all.some((a) => !a.vpn) ? all.filter((a) => !a.vpn) : all;
  let pick = usable[0];
  const qrBox = h("div.phone-qr-code");
  const urlLine = h("code.remote-url");
  const draw = async () => {
    const url = `http://${pick.address}:${r.port}/remote.html`;
    urlLine.textContent = url;
    const svg = await qrSvg(`${url}?code=${encodeURIComponent(r.code)}`, 9).catch(() => null);
    qrBox.replaceChildren(svg || h("p.muted.small", "(no QR code - type the address)"));
  };
  const chips = usable.length > 1 ? h("div.row-btns", ...usable.map((a) => h("button.chip" + (a === pick ? ".on" : ""), {
    title: a.tailscale ? "From anywhere, with Tailscale on the phone" : "A phone on this network",
    onclick: (e) => { pick = a; for (const b of e.target.parentNode.children) b.classList.toggle("on", b === e.target); draw(); },
  }, a.tailscale ? `Tailscale ${a.address}` : a.address))) : null;
  body.replaceChildren(
    h("p", h("b", "Scan with the phone's camera"), " - it opens the remote, already signed in."),
    qrBox, chips,
    h("p.muted.small", "The phone must be on the same Wi-Fi as this computer. Or type ", urlLine, ` and the code ${showCode(r.code)}.`),
    h("p.muted.small", "Anyone who scans this can play the show: \"New code\" in Desk → Other computers, phones and tablets signs every phone out."));
  draw();
}
