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
      <p class="intro">${H.intro}</p>

      <section class="hcard">
        <h3>${H.symptomsTitle}</h3>
        ${H.symptoms.map(([title, steps]) => `
          <details class="symptom"><summary>${esc(title)}</summary>
            <ol>${steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol></details>`).join("")}
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
        <h3>${H.ppTitle}</h3>
        <ol class="steps">${H.ppSteps.map(x => `<li>${esc(x)}</li>`).join("")}</ol>
      </section>

      <section class="hcard">
        <h3>${H.shortcutsTitle}</h3>
        <table class="keys"><tbody>
          ${S.shortcuts.map(([k, what]) => `<tr><th><kbd>${esc(k)}</kbd></th><td>${esc(what)}</td></tr>`).join("")}
        </tbody></table>
      </section>
    </div>`;

  const unsub = store.subscribe(s => {
    s ||= {};
    f.setSetupName(S.toolbar.setupName(s.audio?.device, !(s.pp ? !s.pp.connected : s.pp_error)));
  });

  const DONE = { rehearsal: S.toast.rehearsal, diagnostics: S.toast.diagnostics };
  f.panel.querySelectorAll("[data-do]").forEach(b => {
    b.onclick = async () => {
      b.disabled = true;
      const action = b.dataset.do;
      const res = await send(action, action === "rehearsal" ? { sample: "default" } : {});
      b.disabled = false;
      toast(messageFor(res) || DONE[action], res.ok ? "ok" : res.notBuilt ? "info" : "warn");
      if (res.ok && action === "rehearsal") location.hash = "#live";
    };
  });
  return unsub;
}
