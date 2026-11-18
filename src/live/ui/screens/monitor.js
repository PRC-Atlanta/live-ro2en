// Monitor screen: one row per segment, in three columns: what was transcribed, what was translated
// and what Live Voice said, each with its clock time (and, for the second and third, how long after
// the transcript). Rows come from GET /api/monitor (monitor.py), asked once a second; Clear empties
// them (POST /api/monitor/clear). Same frame as the Live screen.
import { S } from "../i18n/strings.en.js";
import { frame } from "../components/frame.js";
import { toast } from "../components/toast.js";
import { apiUrl } from "../core/api.js";

const M = S.monitor;
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const clock = t => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");
const after = (t, t0) => (t && t0 && t >= t0 ? `+${(t - t0).toFixed(1)} s` : "");

function cell(text, t, t0, { empty, tag, badge = "" } = {}) {
  const when = t ? `${clock(t)}${t0 ? ` <i>${after(t, t0)}</i>` : ""}${tag ? ` <b>${esc(tag)}</b>` : ""}` : "";
  const head = when || badge ? `<span class="mon-t">${when}${badge}</span>` : "";
  return `<td>${head}<div class="${text ? "" : "mon-none"}">${text ? esc(text) : esc(empty || "")}</div></td>`;
}

// the translator badge(s) for the Live Voice column: AI, Local or Local · fallback
const badges = engines => (engines || []).map(e =>
  `<span class="mon-eng" data-engine="${esc(e)}" title="${M.engineTitle}">${esc(M.engine[e] || e)}</span>`).join("");

export function rowHTML(r) {
  return `<tr data-key="${r.key}">` +
    cell(r.heard, r.heard_t, null) +
    cell(r.translated, r.translated_t, r.heard_t, { empty: M.notShown }) +
    cell(r.voiced, r.voiced_t, r.heard_t, { empty: M.notVoiced, tag: r.voice_kind === "summary" ? M.summary : "",
                                            badge: badges(r.engines) }) +
    "</tr>";
}

// The Numbers drawer: phrases by outcome, who translated, and the alerts and warnings.
export function numbersHTML(s) {
  const counts = Object.entries(s.counts || {});
  const used = s.ai?.used;
  const lines = [...(s.alerts || []), ...(s.warnings || []),
                 ...(s.pp_error ? [`ProPresenter: ${s.pp_error}`] : []),
                 ...(s.stt || s.translator ? [M.models(s.stt, s.translator)] : [])];
  const table = rows => `<table>${rows.map(([k, v, tip]) => `<tr${tip ? ` title="${esc(tip)}"` : ""}><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
  return `<section><h4>${M.outcomesTitle}</h4>${
      table(counts.length ? counts.map(([k, v]) => [M.outcomes[k] || M.other, v, k]) : [[M.nothingYet, ""]])}</section>` +
    (used ? `<section><h4>${M.usageTitle}</h4>${table(Object.entries(M.usage).map(([k, label]) => [label, M.usageValue(used[k])]))}</section>` : "") +
    `<section><h4>${M.alertsTitle}</h4><pre>${esc(lines.length ? lines.join("\n") : M.none)}</pre></section>`;
}

export function render(el, store) {
  const f = frame(el);
  f.panel.innerHTML = `
    <div class="mon">
      <div class="mon-stats">
        <span class="mon-delay" data-delay></span>
        <details class="mon-nums"><summary>${M.numbers}</summary><div class="mon-nums-body" data-nums></div></details>
      </div>
      <div class="mon-scroll" data-scroll>
        <table class="mon-table">
          <thead><tr><th>${M.heard}</th><th>${M.translated}</th><th>${M.voiced}</th></tr></thead>
          <tbody data-rows></tbody>
        </table>
        <p class="mon-empty" data-empty>${M.empty}</p>
      </div>
      <div class="mon-foot">
        <span class="sub" data-count></span>
        <button class="btn primary" data-clear>${M.clear}</button>
      </div>
    </div>`;
  const q = sel => f.panel.querySelector(sel);
  const scroll = q("[data-scroll]"), body = q("[data-rows]");
  let rev = -1, stopped = false, timer = 0;

  // keep the newest row in view, unless the operator scrolled up to read an older one
  const atBottom = () => scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 40;

  function show(rows) {
    const follow = atBottom();
    body.innerHTML = rows.map(rowHTML).join("");
    q("[data-empty]").hidden = rows.length > 0;
    q("[data-count]").textContent = rows.length ? M.count(rows.length) : "";
    if (follow) scroll.scrollTop = scroll.scrollHeight;
  }

  async function poll() {
    try {
      const r = await fetch(apiUrl(`monitor?since=${rev}`));
      if (r.ok) {
        const m = await r.json();
        if (m.rows && m.rev !== rev) { rev = m.rev; show(m.rows); }
      }
    } catch { /* the app is restarting: try again in a second */ }
    if (!stopped) timer = setTimeout(poll, 1000);
  }

  q("[data-clear]").onclick = async () => {
    try {
      const r = await fetch(apiUrl("monitor/clear"), { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!r.ok) throw new Error(String(r.status));
      rev = -1; show([]); toast(M.cleared, "ok");
    } catch { toast(S.toast.failed, "warn"); }
  };

  // the numbers for whoever tunes the system: delay in the strip, the rest in the Numbers drawer
  let lastNums = "";
  function numbers(s) {
    q("[data-delay]").textContent = M.delay(s.latency_last, s.latency_median, s.first_word_ok_pct);
    const html = numbersHTML(s);
    if (html !== lastNums) { lastNums = html; q("[data-nums]").innerHTML = html; }
  }
  const onState = s => { s ||= {}; f.update(s); numbers(s); };

  show([]);
  poll();
  const unsub = store.subscribe(onState);
  onState(store.get());
  return () => { stopped = true; clearTimeout(timer); unsub(); };
}
