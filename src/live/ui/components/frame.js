// The plugin-style frame every screen shares (docs/ui_mockup/live.html): a black toolbar strip of
// bevelled buttons, the teal panel with a glowing edge, and the LIVE|TRANSLATE footer.
// frame(el, {onRehearsal, onNames}) -> {panel, setSetupName(text)}.
import { S } from "../i18n/strings.en.js";
import { send, messageFor } from "./actions.js";
import { toast } from "./toast.js";

// Loads styles/live.css once; resolves when it has applied (so nothing flashes unstyled).
let loaded = null;
export function ensureStyles() {
  if (loaded) return loaded;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = new URL("../styles/live.css", import.meta.url).href; l.dataset.liveCss = "";
  loaded = new Promise(res => { l.onload = l.onerror = res; });
  document.head.append(l);
  return loaded;
}

export function frame(el, opts = {}) {
  el.innerHTML = `
    <div class="unit" style="visibility:hidden">
      <div class="tbar">
        <div class="tlogo" aria-hidden="true">LT</div>
        <button class="tb" data-tb="rehearsal" title="${S.toolbar.rehearsalHint}">${S.toolbar.rehearsal}</button>
        <button class="tb wide" data-tb="setup"></button>
        <button class="tb" data-tb="names">${S.toolbar.names}</button>
        <button class="tb" data-tb="settings">${S.toolbar.settings}</button>
        <button class="tb" data-tb="help" title="${S.toolbar.helpHint}" aria-label="${S.toolbar.helpHint}">${S.toolbar.help}</button>
      </div>
      <div class="panel"><div class="panel-body"></div>
        <div class="foot">
          <div class="mark">${S.app.mark[0]}<span></span>${S.app.mark[1]}</div>
          <div class="brand"><b>${S.app.brand}</b>${S.app.offline}</div>
        </div>
      </div>
    </div>`;
  const unit = el.querySelector(".unit");
  ensureStyles().then(() => { unit.style.visibility = ""; });
  const tb = n => el.querySelector(`[data-tb="${n}"]`);
  tb("rehearsal").onclick = opts.onRehearsal || (async () => {
    const res = await send("rehearsal", { sample: "default" });
    toast(messageFor(res) || S.toast.rehearsal, res.ok ? "ok" : "info");
  });
  tb("setup").onclick = () => { location.hash = "#settings"; };
  tb("names").onclick = opts.onNames || (() => { location.hash = "#settings"; });
  tb("settings").onclick = () => { location.hash = "#settings"; };
  tb("help").onclick = () => { location.hash = "#help"; };
  return {
    panel: el.querySelector(".panel-body"),
    setSetupName(t) { if (tb("setup").textContent !== t) tb("setup").textContent = t; },
  };
}
