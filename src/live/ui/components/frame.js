// The plugin-style frame every screen shares (docs/ui_mockup/live.html, APP_PLAN §1.3: Setup,
// Settings, After and Help use the same frame): a black toolbar strip of bevelled buttons, the
// teal panel with a glowing edge, and the LIVE|TRANSLATE footer. It is the app's only header and
// navigation (index.html has none). The button for the screen on show has aria-current="page".
// frame(el, {onRehearsal, onNames}) -> {panel, setSetupName(text), update(state)}.
import { S } from "../i18n/strings.en.js";
import { send, messageFor } from "./actions.js";
import { toast } from "./toast.js";
import { isLive } from "./keys.js";

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

// toolbar button -> the screen it opens (the wide setup button opens Settings)
const GOES = { live: "live", setup: "settings", names: "settings", settings: "settings", after: "after", help: "help" };

export function frame(el, opts = {}) {
  const here = (location.hash.slice(1) || "live");
  el.innerHTML = `
    <div class="unit" style="visibility:hidden">
      <nav class="tbar" aria-label="${S.toolbar.navLabel}">
        <div class="tlogo" aria-hidden="true">LT</div>
        <button class="tb" data-tb="live" title="${S.toolbar.liveHint}">${S.toolbar.live}</button>
        <button class="tb" data-tb="rehearsal" title="${S.toolbar.rehearsalHint}">${S.toolbar.rehearsal}</button>
        <button class="tb wide" data-tb="setup" title="${S.toolbar.setupHint}"></button>
        <button class="tb" data-tb="names">${S.toolbar.names}</button>
        <button class="tb" data-tb="settings">${S.toolbar.settings}</button>
        <button class="tb" data-tb="after" title="${S.toolbar.afterHint}" hidden>${S.toolbar.after}</button>
        <button class="tb" data-tb="help" title="${S.toolbar.helpHint}" aria-label="${S.toolbar.helpHint}">${S.toolbar.help}</button>
      </nav>
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
  const go = name => () => { location.hash = `#${name}`; };
  tb("rehearsal").onclick = opts.onRehearsal || (async () => {
    const res = await send("rehearsal", {});            // no sample: the first recording in samples/
    toast(messageFor(res) || S.toast.rehearsal, res.ok ? "ok" : "info");
  });
  for (const [n, screen] of Object.entries(GOES)) if (!tb(n).onclick) tb(n).onclick = go(screen);
  if (opts.onNames) tb("names").onclick = opts.onNames;
  // only the Live button stands for Live; Settings and Help for themselves
  for (const n of ["live", "settings", "after", "help"]) if (here === n) tb(n).setAttribute("aria-current", "page");
  const api = {
    panel: el.querySelector(".panel-body"),
    setSetupName(t) { if (tb("setup").textContent !== t) tb("setup").textContent = t; },
    // the parts of the toolbar that follow the state: the setup name, and After once a service ran
    update(s) {
      s ||= {};
      api.setSetupName(S.toolbar.setupName(s.audio?.device, !(s.pp ? !s.pp.connected : s.pp_error)));
      const ran = s.elapsed_s != null || Object.keys(s.counts || {}).length > 0;
      tb("after").hidden = !(here === "after" || (ran && !isLive(s)));
    },
  };
  return api;
}
