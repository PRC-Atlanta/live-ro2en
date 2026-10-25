// Live screen (docs/APP_PLAN.md §1.3 "Live", controls §1.4), matching docs/ui_mockup/live.html:
// sound meter, Heard / On screen / Both text display, Hide LED and health pills, problem banner,
// and the control deck (Speaker knob, the big Start/Stop dial with its Delay arc, Quality knob).
import { S } from "../i18n/strings.en.js";
import { frame } from "../components/frame.js";
import { mountMeter } from "../components/meter.js";
import { knob } from "../components/knob.js";
import { gainKnob } from "../components/gainknob.js";
import { intentFor, callFor, spaceGate, isLive } from "../components/keys.js";
import { dialFor, stateLine, delayOf, pillsFor, bannerFor, heardLines, ppStrip } from "../components/status.js";
import { send, messageFor, routeFor } from "../components/actions.js";
import { toast } from "../components/toast.js";
import { ask } from "../components/dialog.js";
import { get } from "../core/api.js";
import { call as settingsCall } from "./setup/kit.js";

const VIEWS = ["heard", "screen", "both"];
const HOLD_MS = 600;
const setHTML = (node, html) => { if (node._html !== html) { node._html = html; node.innerHTML = html; } };
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const DECK = `
  <div class="mirror" data-mirror>
    <div class="mtabs" role="tablist" aria-label="What to show">
      ${VIEWS.map(v => `<button class="mtab" role="tab" data-view="${v}">${S.tabs[v]}</button>`).join("")}
      <span class="sub tabhint">${S.tabs.hint} <span class="hidtag">${S.tabs.hiddenTag}</span></span>
    </div>
    <div class="views" data-views>
      <div class="view v-heard" role="tabpanel">
        <div class="vhead sub" data-heardhead></div>
        <div class="heard" data-heard></div>
      </div>
      <div class="view v-screen" role="tabpanel">
        <div class="vhead sub"><span data-screenhead></span> <span class="hidtag">${S.screenHiddenTag}</span></div>
        <div class="screen" data-screen aria-live="polite"></div>
      </div>
    </div>
    <details class="earlier"><summary class="sub">${S.history}</summary><ol data-history></ol></details>
  </div>

  <div class="ledrow">
    <div class="leds">
      <button class="led" data-hide aria-pressed="false"><b></b><span class="label">${S.hide}</span><span class="sub">${S.hideKey}</span></button>
      <button class="led" data-song aria-pressed="false" title="${S.songTitle}"><b></b><span class="label">${S.song}</span><span class="sub">${S.songKey}</span></button>
    </div>
    <div class="pills">
      <span class="pill" data-pill="sound"><i></i><span></span></span>
      <span class="pill" data-pill="pp"><i></i><span></span></span>
      <span class="pill" data-pill="pace"><i></i><span></span></span>
    </div>
  </div>

  <div class="banner" data-banner role="alert"><p></p><button class="bbtn"></button></div>

  <div class="deck">
    <div class="ctlstack">
      <div class="ctl" data-k-gain></div>
      <div class="ctl" data-k-mode></div>
    </div>
    <div class="ctl center">
      <div class="bigwrap">
        <svg viewBox="0 0 260 230" aria-hidden="true">
          <path d="M 176 13 A 112 112 0 0 1 176 207" fill="none" stroke="#0e2a33" stroke-width="10"/>
          <path data-arc d="M 176 13 A 112 112 0 0 1 176 207" fill="none" stroke="var(--lime)" stroke-width="6"
                pathLength="100" stroke-dasharray="0 100" class="arc"/>
          <circle data-arcdot r="5" fill="var(--cyan-hi)" cx="176" cy="13" class="arcdot"/>
        </svg>
        <div class="arc-lbl"><div class="label">${S.delay}</div><div class="v" data-delay>–</div></div>
        <div class="arc-ticks" style="top:4px;right:94px">0 s</div>
        <div class="arc-ticks" style="top:204px;right:94px">10 s</div>
        <button class="big" data-big aria-describedby="lt-bighint">
          <span class="ring"></span><span class="hold"></span><span class="tri"></span>
          <span class="face"><strong data-bigtxt></strong><small data-bigsub></small></span>
        </button>
      </div>
      <div class="label stateline" data-stateline></div>
      <div class="sub" id="lt-bighint" data-bighint></div>
    </div>
    <div class="ctl" data-k-prof></div>
  </div>
  <div class="sr" data-announce aria-live="polite"></div>`;

export function render(el, store) {
  let s = store.get() || {};
  const unbind = [];
  const f = frame(el, { onNames: () => run("terms") });
  f.panel.innerHTML = `<div class="meterwrap" data-meterwrap><div data-meterbox></div>
    <div class="ppstrip" data-ppstrip role="status" hidden><i></i><span class="t"><strong></strong> · <span></span></span><button type="button"></button></div>
  </div>${DECK}`;
  const q = sel => f.panel.querySelector(sel);
  const meter = mountMeter(q("[data-meterbox]"));

  // input gain knob: saved as live.input_gain_db; the engine applies it from the next frame.
  // A hand turn switches the automatic gain off (saved with the value), so the knob never
  // fights the engine; switching Auto off keeps the gain it reached (no jump).
  let gainTimer = null, gainAuto = !!(s.audio && s.audio.gain_auto), gainNow = 0, autoT = 0;   // autoT: our last switch
  const saveGain = async v => {
    clearTimeout(gainTimer); gainTimer = null;
    const values = { "live.input_gain_db": v };
    if (gainAuto) { values["live.input_gain_auto"] = false; gainAuto = false; gain.setAuto(false); autoT = Date.now(); }
    const r = await settingsCall("PUT", "settings", { values });
    if (!r.ok) toast(S.toast.failed, "warn");
  };
  const gain = gainKnob(q("[data-k-gain]"), {
    label: S.meter.gain, keys: S.meter.gainKeys, min: -12, max: 30, fmt: S.meter.gainDb,
    onChange: v => { clearTimeout(gainTimer); gainTimer = setTimeout(() => saveGain(v), 150); },   // the meter follows while turning
    onCommit: saveGain,
    auto: { label: S.meter.auto, title: S.meter.autoTitle, onToggle: async on => {
      gainAuto = on; gain.setAuto(on); autoT = Date.now();
      const values = { "live.input_gain_auto": on };
      if (!on) values["live.input_gain_db"] = Math.round(gainNow);
      const r = await settingsCall("PUT", "settings", { values });
      if (!r.ok) toast(S.toast.failed, "warn");
    } },
  });
  gain.setAuto(gainAuto);

  let view = "screen";
  try { view = localStorage.getItem("lt.view") || "screen"; } catch { /* private mode */ }
  if (!VIEWS.includes(view)) view = "screen";
  const setView = v => { view = v; try { localStorage.setItem("lt.view", v); } catch { /* ignore */ } paint(); };
  f.panel.querySelectorAll(".mtab").forEach(t => { t.onclick = () => setView(t.dataset.view); });

  const setMode = knob(q("[data-k-mode]"), {
    id: "mode", label: S.mode.label, keys: S.mode.keys, face: S.mode.face,
    positions: [
      { value: "ro-en", label: S.mode.knob["ro-en"], at: "left:0;top:86px", angle: 50 },
      { value: "auto", label: S.mode.knob.auto, at: "left:50%;top:-6px;transform:translateX(-50%)", angle: 180 },
      { value: "en-ro", label: S.mode.knob["en-ro"], at: "right:0;top:86px", angle: -50 },
    ],
    onPick: m => run({ auto: "mode_auto", "ro-en": "mode_ro", "en-ro": "mode_en" }[m]),
  });
  const setProf = knob(q("[data-k-prof]"), {
    id: "prof", label: S.quality.label, keys: S.quality.keys, face: S.quality.face,
    positions: [
      { value: "fast", label: S.quality.knob.fast, at: "left:-10px;top:92px", angle: 50 },
      { value: "quality", label: S.quality.knob.quality, at: "right:-10px;top:92px", angle: -50 },
    ],
    onPick: p => { if (p !== s.profile) run("quality"); },
  });

  // ---- actions ----------------------------------------------------------------------------
  const say = t => { q("[data-announce]").textContent = t; };
  const DONE = {
    pause: b => (b.on ? S.toast.hidden : S.toast.shown), blank: () => S.toast.blanked,
    song: b => (b.on ? S.toast.songOn : S.toast.songOff),
    retract: () => S.toast.removed, correct: () => S.toast.fixed, reconnect: () => S.toast.reconnecting,
    start: () => S.toast.started, stop: () => S.toast.stopped,
    mode: b => S.toast.mode(S.mode.long[b.mode]), profile: b => S.toast.quality(S.quality.long[b.profile] || b.profile),
    terms: b => S.toast.termsSaved(b.lines.length),
  };
  async function post(call) {
    const [action, body] = call;
    const res = await send(action, body);
    if (res.ok && res.data && typeof res.data === "object" && "running" in res.data) store.set({ ...store.get(), ...res.data });
    const msg = messageFor(res, action) || DONE[action]?.(body);
    const calm = res.notBuilt || res.data?.error === "nothing_to_change";
    toast(msg, res.ok ? "ok" : calm ? "info" : "warn");
    if (res.ok && msg) say(msg);
    return res;
  }
  async function run(intent) {
    if (intent === "view") return setView(VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length]);
    if (intent === "pp_on") {                            // the strip's "Turn on": Settings → ProPresenter on
      const r = await settingsCall("PUT", "settings", { values: { "propresenter.enabled": true } });
      toast(r.ok ? S.toast.ppOn : S.toast.failed, r.ok ? "ok" : "warn");
      return;
    }
    if (intent === "fix") {
      const last = [...(s.lines || [])].reverse().find(l => l.dst);
      if (!last) return toast(S.toast.nothingToFix, "info");
      const r = await ask({ title: S.fix.title, hint: S.fix.hint, value: last.dst, ok: S.fix.ok, cancel: S.fix.cancel });
      if (r && r.text.trim()) post(callFor("correct", s, { text: r.text.trim() }));
      return;
    }
    if (intent === "terms") {
      // the list replaces today's list, so start from what is already there
      let now = [];
      try { now = (await get("terms")).lines || []; } catch { /* no answer: start empty */ }
      const r = await ask({ title: S.terms.title, hint: S.terms.hint, value: now.join("\n"), multiline: true,
                            check: S.terms.save, ok: S.terms.ok, cancel: S.terms.cancel });
      if (!r) return;                                    // cancelled: today's list stays as it was
      const lines = r.text.split("\n").map(x => x.trim()).filter(Boolean);
      if (lines.length || now.length) post(callFor("terms", s, { lines, save: r.checked }));
      return;
    }
    const call = callFor(intent, s);
    if (call) return post(call);
    const route = routeFor(intent);
    if (route) { location.hash = route; return; }
    return post([intent, {}]);                           // a backend-named action we don't know yet
  }

  // ---- Start / Stop: Space (twice, or held) and the dial (click to start, hold to stop) ----
  const gate = spaceGate(500);
  let spaceTimer = 0;
  const big = q("[data-big]");
  const holdStart = () => { big.classList.add("holding"); };
  const holdEnd = () => { big.classList.remove("holding"); };
  function pressStart() {
    const g = gate(s);
    if (g === "start") run("start");
    else if (g === "stop") { holdEnd(); run("stop"); }
    else if (g === "armed") { toast(S.toast.pressAgain, "info", 1200); say(S.toast.pressAgain); }
    else if (s.phase === "needs_setup") location.hash = "#setup";
  }
  let dialTimer = 0, dialHeld = false;
  big.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !isLive(s)) return;
    dialHeld = false; holdStart();
    dialTimer = setTimeout(() => { dialHeld = true; holdEnd(); run("stop"); }, HOLD_MS);
  });
  const cancelDial = () => { clearTimeout(dialTimer); holdEnd(); };
  big.addEventListener("pointerup", cancelDial);
  big.addEventListener("pointerleave", cancelDial);
  big.addEventListener("click", e => {
    if (dialHeld) { dialHeld = false; return; }
    if (e.detail === 0) return pressStart();             // Enter on the focused dial acts like Space
    if (isLive(s)) { toast(S.holdToStop, "info", 1800); return; }
    if (s.phase === "needs_setup") { location.hash = "#setup"; return; }
    if (!s.offline && ["ready", "stopped", "error"].includes(s.phase ?? "ready")) run("start");
  });

  const onKey = e => {
    if (e.target.closest?.("input, textarea, select, dialog, [contenteditable]")) return;
    const intent = intentFor(e);
    if (!intent) return;
    e.preventDefault();
    if (intent === "toggle") {
      if (e.repeat) return;
      const wasLive = isLive(s);
      pressStart();
      if (wasLive) {                                     // holding Space for 0.6 s also stops
        holdStart();
        spaceTimer = setTimeout(() => { if (isLive(s)) { holdEnd(); run("stop"); } }, HOLD_MS);
      }
      return;
    }
    if (e.repeat) return;
    run(intent);
  };
  const onKeyUp = e => { if (e.key === " ") { clearTimeout(spaceTimer); holdEnd(); } };
  addEventListener("keydown", onKey);
  addEventListener("keyup", onKeyUp);
  unbind.push(() => { removeEventListener("keydown", onKey); removeEventListener("keyup", onKeyUp); });

  q("[data-hide]").onclick = () => run("pause");
  q("[data-song]").onclick = () => run("song");
  q("[data-banner] .bbtn").onclick = () => { const b = bannerFor(s); if (b) run(b.action.id); };
  q("[data-ppstrip] button").onclick = () => { const p = ppStrip(s); if (p) run(p.action.id); };

  // ---- paint ------------------------------------------------------------------------------
  let lastPhase = null;
  let liveSince = null;                                  // until the backend sends elapsed_s
  function paint() {
    if (isLive(s)) liveSince ??= Date.now(); else liveSince = null;
    const d = dialFor(s.elapsed_s == null && liveSince ? { ...s, elapsed_s: (Date.now() - liveSince) / 1000 } : s);
    big.dataset.ring = d.ring;
    q("[data-bigtxt]").textContent = d.big;
    q("[data-bigsub]").textContent = d.small;
    big.setAttribute("aria-label", `${d.small}${d.live ? "" : `. ${d.hint}`}`);
    q("[data-bighint]").textContent = d.hint;
    q("[data-stateline]").textContent = stateLine(s);
    if (d.phase !== lastPhase) { if (lastPhase !== null) say(S.phaseWord[d.phase] || d.phase); lastPhase = d.phase; }

    setMode(s.mode || "auto");
    setProf(s.profile || "fast");

    const hidden = !!s.paused;
    const hb = q("[data-hide]");
    hb.classList.toggle("on", hidden); hb.setAttribute("aria-pressed", hidden);
    q("[data-mirror]").classList.toggle("hidden", hidden);
    const sb = q("[data-song]"), song = !!s.song;
    sb.classList.toggle("on", song); sb.setAttribute("aria-pressed", song);
    sb.querySelector(".label").textContent = song && s.song_auto ? S.songAuto : S.song;

    // the speaker's language and the captions' language
    const srcLang = s.mode === "en-ro" ? "en" : s.mode === "ro-en" ? "ro" : s.lang || "ro";
    const dstLang = srcLang === "en" ? "ro" : "en";
    q("[data-heardhead]").textContent = S.heardHead(S.lang[srcLang]);
    q("[data-screenhead]").textContent = S.screenHead(S.lang[dstLang]);
    const hv = heardLines(s, view === "heard" ? 4 : 2);
    setHTML(q("[data-heard]"), hv.length
      ? hv.map((t, i) => `<div class="${i < hv.length - 1 ? "old" : ""}">${esc(t)}</div>`).join("")
      : `<div class="empty">${S.nothingYet.heard}</div>`);
    const scr = q("[data-screen]");
    const screenText = s.screen || "";
    if (scr.dataset.text !== screenText) {
      scr.dataset.text = screenText;
      const txt = document.createElement("div");           // inner block: the compact layout keeps its last lines
      txt.textContent = screenText || S.nothingYet.screen;
      scr.replaceChildren(txt);
      scr.classList.toggle("empty", !screenText);
    }
    q("[data-views]").dataset.view = view;
    f.panel.querySelectorAll(".mtab").forEach(t => t.setAttribute("aria-selected", t.dataset.view === view));
    const hist = (s.lines || []).slice(-10).reverse();
    setHTML(q("[data-history]"), hist.length
      ? hist.map(l => `<li><span class="src">${esc(l.src)}</span><span class="dst${l.dst ? "" : " none"}">${l.dst ? esc(l.dst) : S.notShown}</span></li>`).join("")
      : `<li class="empty">${S.historyEmpty}</li>`);

    // delay arc: 0–10 s, amber over 6 s
    const dl = delayOf(s), pct = dl == null ? 0 : Math.min(100, dl * 10);
    const arc = q("[data-arc]");
    arc.setAttribute("stroke-dasharray", `${pct} 100`);
    arc.setAttribute("stroke", dl > 6 ? "var(--amber)" : "var(--lime)");
    const a = (-60 + pct * 1.2) * Math.PI / 180;
    q("[data-arcdot]").setAttribute("cx", 120 + 112 * Math.cos(a));
    q("[data-arcdot]").setAttribute("cy", 110 + 112 * Math.sin(a));
    const dv = q("[data-delay]");
    dv.textContent = dl == null ? "–" : `${dl.toFixed(1)} s`;
    dv.title = S.delayTitle(s.latency_last, s.latency_median);

    const pills = pillsFor(s, meter.key());
    for (const [k, [cls, txt]] of Object.entries(pills)) {
      const p = q(`[data-pill="${k}"]`);
      p.className = `pill ${cls}`;
      p.dataset.state = cls || "ok";
      if (p.lastChild.textContent !== txt) p.lastChild.textContent = txt;
    }

    const ps = ppStrip(s), strip = q("[data-ppstrip]");
    q("[data-meterwrap]").classList.toggle("pp-alert", !!ps);
    strip.hidden = !ps;
    if (ps) {
      strip.dataset.code = ps.code;
      strip.querySelector("strong").textContent = ps.title;
      strip.querySelector(".t > span").textContent = ps.msg;
      strip.querySelector("button").textContent = ps.action.label;
    }

    const b = bannerFor(s), bn = q("[data-banner]");
    bn.classList.toggle("show", !!b);
    if (b) {
      bn.dataset.severity = b.severity; bn.dataset.code = b.code;
      if (bn.firstChild.textContent !== b.msg) bn.firstChild.textContent = b.msg;
      bn.lastChild.textContent = b.action.label;
    } else { delete bn.dataset.code; }

    f.update(s);
  }

  unbind.push(store.subscribe(next => {
    s = next || {};
    if (s.audio) meter.update(s.audio);
    if (s.audio && typeof s.audio.gain_db === "number" && !gainTimer) { gainNow = s.audio.gain_db; gain.set(s.audio.gain_db); }
    // the switch follows the saved setting, but not a state sent before our own switch landed
    if (s.audio && typeof s.audio.gain_auto === "boolean" && !gainTimer && s.audio.gain_auto !== gainAuto
        && Date.now() - autoT > 2000) {
      gainAuto = s.audio.gain_auto; gain.setAuto(gainAuto);
    }
    paint();
  }));
  // the elapsed clock and the pills follow the meter between state updates
  const t = setInterval(paint, 500);
  unbind.push(() => clearInterval(t), () => meter.destroy());
  return () => unbind.forEach(fn => fn());
}
