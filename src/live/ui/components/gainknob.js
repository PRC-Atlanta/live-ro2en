// A continuous knob (unlike knob.js, which clicks between positions): drag up/down or
// scroll to turn it, arrow keys step 1 dB (Page Up/Down 6 dB), double-click goes back to 0.
// gainKnob(el, {label, keys, min, max, fmt, onChange, onCommit, auto}) -> {set(value), setAuto(on), busy()}.
// onChange fires while turning, onCommit when the turn ends (pointer up, a key, a pause in
// scrolling). auto = {label, title, onToggle(on)} adds a small Auto switch beside the label:
// while it is on, the knob follows the engine's automatic gain (set) and shows it in cyan.
const SWEEP = 270;                                       // degrees from min to max

export function gainKnob(el, { label, keys, min, max, fmt, onChange, onCommit, auto }) {
  el.innerHTML = `
    <div class="knobwrap gk">
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <path class="gk-track" d="${arc(0, 1)}"/>
        <path class="gk-fill" data-fill d=""/>
        <line class="gk-zero" data-zero/>
      </svg>
      <div class="knob" role="slider" tabindex="0" aria-label="${label}" aria-valuemin="${min}" aria-valuemax="${max}"
           title="${keys}"><span class="ptr"></span><span class="val"></span></div>
    </div>
    <div class="gk-foot"><span class="label">${label}</span>${auto
      ? `<button type="button" class="gk-auto" aria-pressed="false" title="${auto.title}">${auto.label}</button>` : ""}</div>`;
  const k = el.querySelector(".knob"), ptr = k.querySelector(".ptr"), val = k.querySelector(".val");
  const fill = el.querySelector("[data-fill]");
  const frac = v => (v - min) / (max - min);
  const z = polar(frac(0)), zi = polar(frac(0), 46);
  Object.entries({ x1: zi.x, y1: zi.y, x2: z.x, y2: z.y }).forEach(([a, n]) => el.querySelector("[data-zero]").setAttribute(a, n));
  let cur = null, turning = false, wheelT = null;
  const autoBtn = el.querySelector(".gk-auto");
  const setAuto = on => {
    if (!autoBtn) return;
    autoBtn.setAttribute("aria-pressed", String(!!on));
    el.querySelector(".gk").classList.toggle("auto", !!on);
  };
  if (autoBtn) autoBtn.addEventListener("click", () => auto.onToggle(autoBtn.getAttribute("aria-pressed") !== "true"));

  function show(v) {
    cur = v;
    ptr.style.transform = `rotate(${180 - SWEEP / 2 + frac(v) * SWEEP}deg) translateY(${Math.round((k.offsetWidth || 84) * 0.32)}px)`;
    val.textContent = fmt(v);
    const a = frac(Math.min(0, v)), b = frac(Math.max(0, v));
    fill.setAttribute("d", a === b ? "" : arc(a, b));
    k.setAttribute("aria-valuenow", v);
    k.setAttribute("aria-valuetext", fmt(v));
  }
  function turn(v) {
    v = Math.round(Math.min(max, Math.max(min, v)));
    if (v === cur) return;
    show(v);
    onChange(v);
  }
  const commit = () => onCommit(cur);

  k.addEventListener("pointerdown", e => {
    turning = true;
    k.setPointerCapture(e.pointerId);
    const y0 = e.clientY, v0 = cur;
    k.onpointermove = m => turn(v0 + (y0 - m.clientY) / 4);   // 4 px per dB
    k.onpointerup = k.onpointercancel = () => {
      k.onpointermove = k.onpointerup = k.onpointercancel = null;
      turning = false;
      commit();
    };
  });
  k.addEventListener("wheel", e => {
    e.preventDefault();
    turning = true;
    turn(cur + (e.deltaY < 0 ? 1 : -1));
    clearTimeout(wheelT);
    wheelT = setTimeout(() => { turning = false; commit(); }, 300);
  }, { passive: false });
  k.addEventListener("keydown", e => {
    const step = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 6, PageDown: -6 }[e.key];
    const to = step ? cur + step : e.key === "Home" ? min : e.key === "End" ? max : e.key === "0" ? 0 : null;
    if (to === null) return;
    e.preventDefault();
    e.stopPropagation();                                   // not the screen's shortcuts
    turn(to);
    commit();
  });
  k.addEventListener("dblclick", () => { turn(0); commit(); });

  show(0);
  return {
    set(v) { if (!turning && document.activeElement !== k && v !== cur) show(Math.round(v)); },
    setAuto,
    busy: () => turning,
  };
}

// points on the ring, f = 0..1 along the sweep (0 at bottom-left, clockwise)
function polar(f, r = 54) {
  const a = (90 + (360 - SWEEP) / 2 + f * SWEEP) * Math.PI / 180;
  return { x: +(60 + r * Math.cos(a)).toFixed(2), y: +(60 + r * Math.sin(a)).toFixed(2) };
}
function arc(f0, f1) {
  const p0 = polar(f0), p1 = polar(f1), big = (f1 - f0) * SWEEP > 180 ? 1 : 0;
  return `M ${p0.x} ${p0.y} A 54 54 0 ${big} 1 ${p1.x} ${p1.y}`;
}
