// Small DOM helpers: element builder, toasts, modals, menus, faders.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h("div.cls#id", {attrs}, ...children) */
export function h(tag, attrs, ...children) {
  const m = /^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i.exec(tag) || [];
  const el = document.createElement(m[1] || "div");
  for (const part of (m[2] || "").match(/[.#][\w-]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1));
    else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// ------------------------------------------------------------------ toasts
export function toast(text, kind = "", ms = 2600) {
  const box = $("#toasts");
  const t = h("div.toast" + (kind ? "." + kind : ""), text);
  box.append(t);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => t.remove(), kind === "bad" ? Math.max(ms, 5000) : ms);
}

// ------------------------------------------------------------------ modals
const stack = [];

export function modal({ title, body, foot, wide = false, onClose }) {
  const close = () => {
    scrim.remove();
    const i = stack.indexOf(close);
    if (i >= 0) stack.splice(i, 1);
    if (onClose) onClose();
  };
  const scrim = h("div.modal-scrim", { onmousedown: (e) => { if (e.target === scrim) close(); } },
    h("div.modal" + (wide ? ".wide" : ""), { role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div.modal-head", h("h2", title), h("button.icon-x", { "aria-label": "Close", onclick: () => close() }, "×")),
      h("div.modal-body", body),
      foot ? h("div.modal-foot", foot) : null));
  $("#modal-root").append(scrim);
  stack.push(close);
  const first = scrim.querySelector("input, select, textarea, button.primary");
  if (first) setTimeout(() => first.focus(), 30);
  return close;
}

export function closeTopModal() {
  const close = stack[stack.length - 1];
  if (close) { close(); return true; }
  return false;
}

export const anyModal = () => stack.length > 0;

export function confirmBox(title, text, { ok = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); close(); } };
    const close = modal({
      title,
      body: h("p", { style: { margin: 0, whiteSpace: "pre-wrap" } }, text),
      foot: [
        h("button.btn", { onclick: () => finish(false) }, "Cancel"),
        h("button.btn" + (danger ? ".danger" : ".primary"), { onclick: () => finish(true) }, ok),
      ],
      onClose: () => { if (!done) { done = true; resolve(false); } },
    });
  });
}

export function promptBox(title, label, value = "", { ok = "OK", placeholder = "" } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const input = h("input", { type: "text", value, placeholder, style: { width: "100%" } });
    const finish = (v) => { if (!done) { done = true; resolve(v); close(); } };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(input.value.trim()); });
    const close = modal({
      title,
      body: h("label.field", h("span", label), input),
      foot: [
        h("button.btn", { onclick: () => finish(null) }, "Cancel"),
        h("button.btn.primary", { onclick: () => finish(input.value.trim()) }, ok),
      ],
      onClose: () => { if (!done) { done = true; resolve(null); } },
    });
    setTimeout(() => { input.focus(); input.select(); }, 30);
  });
}

// ------------------------------------------------------------------- menus
let openMenu = null;
export function menu(anchor, items) {
  if (openMenu) openMenu();
  const box = h("div.menu", { role: "menu" });
  for (const it of items) {
    if (it === "-") { box.append(h("hr")); continue; }
    if (!it) continue;
    box.append(h("button" + (it.danger ? ".danger" : ""), {
      role: "menuitem", disabled: it.disabled,
      onclick: () => { close(); it.run(); },
    }, it.label, it.hint ? h("small", it.hint) : null));
  }
  document.body.append(box);
  const r = anchor.getBoundingClientRect();
  const w = box.offsetWidth, ht = box.offsetHeight;
  box.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  box.style.top = (r.bottom + ht + 8 > innerHeight ? Math.max(8, r.top - ht - 4) : r.bottom + 4) + "px";
  const away = (e) => { if (!box.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  function close() {
    box.remove();
    document.removeEventListener("mousedown", away, true);
    document.removeEventListener("keydown", esc, true);
    openMenu = null;
  }
  setTimeout(() => {
    document.addEventListener("mousedown", away, true);
    document.addEventListener("keydown", esc, true);
  });
  openMenu = close;
  return close;
}

// ---------------------------------------------------------------- faders
/**
 * A vertical fader.  onInput fires while dragging (throttled by the
 * caller), onChange on release.  set(value, {mixed, idle}) redraws it
 * without firing - unless the operator is holding it.
 */
export function vfader(el, { min = 0, max = 100, onInput, onChange } = {}) {
  el.replaceChildren(h("div.fill"), h("div.cap"));
  const fill = el.firstChild, cap = el.lastChild;
  let value = min, dragging = false;
  const draw = (v) => {
    const t = (v - min) / (max - min);
    const pct = Math.max(0, Math.min(1, t));
    fill.style.height = `calc(${pct * 100}% - ${pct * 6}px)`;
    cap.style.bottom = `calc(${pct * 100}% - ${pct * 6}px + 3px)`;
  };
  const fromY = (y) => {
    const r = el.getBoundingClientRect();
    const t = 1 - (y - r.top - 3) / Math.max(1, r.height - 6);
    return Math.round(min + Math.max(0, Math.min(1, t)) * (max - min));
  };
  el.addEventListener("pointerdown", (e) => {
    dragging = true;
    el.setPointerCapture(e.pointerId);
    value = fromY(e.clientY);
    draw(value);
    if (onInput) onInput(value);
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const v = fromY(e.clientY);
    if (v === value) return;
    value = v;
    draw(value);
    if (onInput) onInput(value);
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    if (onChange) onChange(value);
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("wheel", (e) => {
    e.preventDefault();
    value = Math.max(min, Math.min(max, value + (e.deltaY < 0 ? 1 : -1) * (e.shiftKey ? 10 : 2)));
    draw(value);
    if (onInput) onInput(value);
    if (onChange) onChange(value);
  }, { passive: false });
  el.tabIndex = 0;
  el.setAttribute("role", "slider");
  el.setAttribute("aria-valuemin", min);
  el.setAttribute("aria-valuemax", max);
  el.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 1;
    let v = value;
    if (e.key === "ArrowUp" || e.key === "ArrowRight") v += step;
    else if (e.key === "ArrowDown" || e.key === "ArrowLeft") v -= step;
    else if (e.key === "Home") v = min;
    else if (e.key === "End") v = max;
    else return;
    e.preventDefault();
    e.stopPropagation();
    value = Math.max(min, Math.min(max, v));
    draw(value);
    if (onInput) onInput(value);
    if (onChange) onChange(value);
  });
  draw(value);
  return {
    set(v, { mixed = false, idle = false } = {}) {
      el.classList.toggle("mixed", mixed);
      el.classList.toggle("idle", idle);
      if (dragging) return;
      value = v;
      el.setAttribute("aria-valuenow", v);
      draw(v);
    },
    get dragging() { return dragging; },
  };
}

/** Throttle to at most one call per `ms`, always delivering the last. */
export function throttle(fn, ms) {
  let last = 0, timer = 0, pending = null;
  return (...args) => {
    pending = args;
    const now = performance.now();
    const wait = ms - (now - last);
    if (wait <= 0) {
      last = now;
      clearTimeout(timer);
      timer = 0;
      fn(...pending);
      pending = null;
    } else if (!timer) {
      timer = setTimeout(() => {
        last = performance.now();
        timer = 0;
        if (pending) fn(...pending);
        pending = null;
      }, wait);
    }
  };
}

export function typingInField(e) {
  const t = e.target;
  return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}

export const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
