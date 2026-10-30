// Shared pieces for the Setup, Settings and After screens (S5b): backend calls that never
// throw, a tiny element builder, and the plugin-look form controls. No top-level DOM access,
// so the logic can be imported by the node tests.

import { frame } from "../../components/frame.js";
import { apiUrl } from "../../core/api.js";

const MOCK = typeof location !== "undefined" && new URLSearchParams(location.search).has("mock");

// call("GET", "setup") -> {ok, status, data, notBuilt}. With ?mock=..., mock_state.js answers.
// A planned endpoint answers 501 {error:"not_implemented"}; that comes back as notBuilt: true,
// so a screen can say "not available in this version yet" instead of breaking.
export async function call(method, path, body) {
  try {
    if (MOCK) {
      const { mockApi } = await import("../../mock_state.js");
      return wrap(await mockApi(method, path, body));
    }
    const r = await fetch(apiUrl(path), {
      method, headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await r.json(); } catch { data = null; }
    return wrap({ status: r.status, data });
  } catch (e) {
    return { ok: false, status: 0, data: null, notBuilt: false, offline: true };
  }
}

export function wrap({ status, data }) {
  const notBuilt = status === 501 || (data && data.error === "not_implemented");
  return { ok: status >= 200 && status < 300 && !notBuilt, status, data, notBuilt };
}

// h("div.card#x", {onclick}, child, "text", [more]) -> element
export function h(sel, attrs, ...kids) {
  const [, tag = "div", rest = ""] = sel.match(/^([a-z0-9-]*)(.*)$/i);
  const el = document.createElement(tag || "div");
  for (const part of rest.match(/[.#][^.#]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs); attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else if (k in el && k !== "list" && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}

// Load this folder's stylesheet once.
export function useStyles() {
  if (document.getElementById("s5b-css")) return;
  const href = new URL("./forms.css", import.meta.url).href;
  document.head.append(h("link#s5b-css", { rel: "stylesheet", href }));
}

// Put a screen's sections inside the shared frame (components/frame.js: toolbar, teal panel,
// footer), like the Live and Help screens. Returns the cleanup for the toolbar's state updates.
export function screenFrame(el, store, ...kids) {
  const f = frame(el);
  f.panel.append(h("div.fx-screen", ...kids));
  return store.subscribe(s => f.update(s));
}

// A section with a title row.
export function panel(title, sub) {
  return h("section.fx-panel", h("div.fx-head", h("h2.fx-label", title), sub ? h("span.fx-sub", sub) : null));
}

// A row of 2-3 click-to-select positions (the knob stand-in for forms). onPick(value).
export function choice(name, options, value, onPick, { disabled = {} } = {}) {
  const box = h("div.fx-choice", { role: "radiogroup", "aria-label": name });
  const btns = options.map(([v, label, hint]) => {
    const b = h("button.fx-pos", { type: "button", role: "radio", "data-value": v },
      h("strong", label), hint ? h("small", hint) : null);
    if (disabled[v]) { b.disabled = true; b.title = disabled[v]; }
    b.addEventListener("click", () => { set(v); onPick(v); });
    return b;
  });
  const set = v => btns.forEach(b => b.setAttribute("aria-checked", String(b.dataset.value === String(v))));
  set(value);
  box.append(...btns);
  box.set = set;
  return box;
}

// An LED toggle like "Hide captions" on the Live screen.
export function led(label, on, onChange) {
  const b = h("button.fx-led", { type: "button", role: "switch" }, h("b"), h("span.fx-label", label));
  const set = v => { b.classList.toggle("on", !!v); b.setAttribute("aria-checked", String(!!v)); };
  set(on);
  b.addEventListener("click", () => { const v = !b.classList.contains("on"); set(v); onChange(v); });
  b.set = set;
  return b;
}

export function field(label, input, hint) {
  return h("label.fx-field", h("span.fx-label", label), input, hint ? h("span.fx-hint", hint) : null);
}

export function button(label, onclick, cls = "") {
  return h(`button.fx-btn${cls}`, { type: "button", onclick }, label);
}

// A short live status line (aria-live) with a colour role: "", good, warn, bad.
export function note() {
  const el = h("p.fx-note", { "aria-live": "polite" });
  el.say = (text, tone = "") => { el.textContent = text || ""; el.dataset.tone = tone; };
  return el;
}

export const NOT_BUILT = "Not available in this version yet.";

// "12:04" or "1:02:30"
export function clock(s) {
  if (s == null || !isFinite(s)) return "–";
  s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor(s / 60) % 60, ss = String(s % 60).padStart(2, "0");
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

export const MODES = [
  ["auto", "Auto-detect", "Either language"],
  ["ro-en", "Romanian speaker", "English captions"],
  ["en-ro", "English speaker", "Romanian captions"],
];
export const PROFILES = [["fast", "Quickest", "Shortest delay"], ["quality", "Best quality", "Smoother sentences"]];
