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

export function render(el, store) {
  const f = frame(el);
  f.panel.innerHTML = `
    <div class="mon">
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

  show([]);
  poll();
  const unsub = store.subscribe(s => f.update(s || {}));
  f.update(store.get() || {});
  return () => { stopped = true; clearTimeout(timer); unsub(); };
}
