// Hash routes -> screen modules. Each screen exports render(el, store) and may return a
// cleanup function. S5a owns live/help, S5b owns setup/settings/after.
const ROUTES = {
  live: () => import("../screens/live.js"),
  setup: () => import("../screens/setup/index.js"),
  settings: () => import("../screens/settings/index.js"),
  after: () => import("../screens/after.js"),
  help: () => import("../screens/help.js"),
};

// First launch: the first state the backend sends says the models are missing, and nobody asked
// for a particular screen, so open the Setup wizard. Once per page load only: leaving the wizard
// stays left (the dial still says "Finish setup first").
export function firstLaunchRoute(state, hash) {
  if (!state || state.offline || state.phase === undefined) return undefined;  // not heard from yet
  return state.phase === "needs_setup" && (hash === "" || hash === "#" || hash === "#live") ? "#setup" : null;
}

// only: one screen and no other (Live Remote's full view shows the Live screen alone)
export function startRouter(el, store, { only = null } = {}) {
  let cleanup = null, seq = 0;
  const show = async () => {
    const name = only || (location.hash.slice(1) in ROUTES ? location.hash.slice(1) : "live");
    const mine = ++seq;
    const mod = await ROUTES[name]();
    if (mine !== seq) return;               // another hash change came in while this one loaded
    if (cleanup) cleanup();                 // the screen's toolbar (components/frame.js) marks where we are
    el.replaceChildren();
    cleanup = mod.render(el, store) || null;
  };
  addEventListener("hashchange", show);
  show();
  if (only) return;                         // and no Setup wizard on first launch either
  let unsub = null, decided = false;
  unsub = store.subscribe(s => {
    if (decided) return;
    const to = firstLaunchRoute(s, location.hash);
    if (to === undefined) return;
    decided = true;
    if (unsub) unsub();
    if (to) location.hash = to;
  });
  if (decided) unsub();
}
