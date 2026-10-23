// Hash routes -> screen modules. Each screen exports render(el, store) and may return a
// cleanup function. S5a owns live/help, S5b owns setup/settings/after.
const ROUTES = {
  live: () => import("../screens/live.js"),
  setup: () => import("../screens/setup/index.js"),
  settings: () => import("../screens/settings/index.js"),
  after: () => import("../screens/after.js"),
  help: () => import("../screens/help.js"),
};

export function startRouter(el, store) {
  let cleanup = null, seq = 0;
  const show = async () => {
    const name = location.hash.slice(1) in ROUTES ? location.hash.slice(1) : "live";
    const mine = ++seq;
    const mod = await ROUTES[name]();
    if (mine !== seq) return;               // another hash change came in while this one loaded
    if (cleanup) cleanup();                 // the screen's toolbar (components/frame.js) marks where we are
    el.replaceChildren();
    cleanup = mod.render(el, store) || null;
  };
  addEventListener("hashchange", show);
  show();
}
