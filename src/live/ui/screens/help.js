// Help screen (docs/APP_PLAN.md §1.3): symptom list, Run a rehearsal, Copy diagnostics, the
// ProPresenter setup steps and the keyboard shortcuts. Same frame as the Live screen.
import { S } from "../i18n/strings.en.js";
import { frame } from "../components/frame.js";
import { send, messageFor } from "../components/actions.js";
import { toast } from "../components/toast.js";

const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const H = S.help;

export function render(el, store) {
  const f = frame(el);
  f.panel.innerHTML = `
    <div class="help">
      <div class="help-head">
        <h2>${H.title}</h2>
        <a class="btn" href="#live">${H.back}</a>
      </div>

      <section class="hcard">
        <h3>${H.symptomsTitle}</h3>
        ${H.symptoms.map(x => (typeof x === "string" ? `<h4 class="hgroup">${esc(x)}</h4>` : `
          <details class="symptom"><summary>${esc(x[0])}</summary>
            <ol>${x[1].map(step => `<li>${esc(step)}</li>`).join("")}</ol></details>`)).join("")}
      </section>

      <section class="hcard">
        <h3>${H.shortcutsTitle}</h3>
        <dl class="hkeys">
          ${S.shortcuts.map(([k, what]) => `<div><dt><kbd>${esc(k)}</kbd></dt><dd>${esc(what)}</dd></div>`).join("")}
        </dl>
      </section>

      <div class="hgrid">
        <section class="hcard">
          <h3>${H.rehearsalTitle}</h3><p>${H.rehearsalText}</p>
          <button class="btn primary" data-do="rehearsal">${H.rehearsalBtn}</button>
        </section>
        <section class="hcard">
          <h3>${H.diagTitle}</h3><p>${H.diagText}</p>
          <button class="btn primary" data-do="diagnostics">${H.diagBtn}</button>
        </section>
      </div>

      <section class="hcard">
        <details class="symptom hpp"><summary>${H.ppTitle}</summary>
          <ol class="steps">${H.ppSteps.map(x => `<li>${esc(x)}</li>`).join("")}</ol></details>
      </section>
    </div>`;

  const unsub = store.subscribe(s => {
    s ||= {};
    f.update(s);
  });

  const DONE = { rehearsal: S.toast.rehearsal, diagnostics: S.toast.diagnostics };
  f.panel.querySelectorAll("[data-do]").forEach(b => {
    b.onclick = async () => {
      b.disabled = true;
      const action = b.dataset.do;
      const res = await send(action, {});                 // rehearsal: the first recording in samples/
      b.disabled = false;
      // the backend falls back from the Desktop to the app's log folder: say where it really is
      const where = res.ok && action === "diagnostics" && res.data?.path && !/\/Desktop\/[^/]+$/.test(res.data.path)
        ? S.toast.savedAt(res.data.path) : null;
      toast(messageFor(res) || where || DONE[action], res.ok ? "ok" : res.notBuilt ? "info" : "warn");
      if (res.ok && action === "rehearsal") location.hash = "#live";
    };
  });
  return unsub;
}
