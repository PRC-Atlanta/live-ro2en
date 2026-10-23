// Sends a Live-screen action and says what happened in plain words. A planned endpoint answers
// 501 {error:"not_implemented"} until its session lands: that is shown as a gentle note, never
// an error. Every call is also kept in window.__liveCalls (newest last) for checking by hand.
import { S } from "../i18n/strings.en.js";

// banner/action ids that open a screen instead of calling the backend
const ROUTES = { help: "#help", settings: "#settings", setup: "#setup", after: "#after", names: "#settings" };

export function routeFor(id) { return ROUTES[id] || null; }

// With ?mock there is no backend: answer as the real one would today, from contract.json
// (a built endpoint says ok, a planned one says 501).
const MOCK = typeof location !== "undefined" && new URLSearchParams(location.search).has("mock");
let contract = null;
async function mockAnswer(action) {
  contract ||= await fetch(new URL("../contract.json", import.meta.url)).then(r => r.json()).catch(() => ({ endpoints: [] }));
  const e = contract.endpoints.find(x => x.method === "POST" && x.path === `/api/${action}`);
  if (!e) return [404, null];
  return e.built ? [200, {}] : [501, { error: "not_implemented", endpoint: `POST /api/${action}`, owner: e.owner }];
}

export async function send(action, body = {}) {
  const rec = { action, body, status: null, t: Date.now() };
  (window.__liveCalls ||= []).push(rec);
  if (MOCK) {
    const [status, data] = await mockAnswer(action);
    rec.status = status;
    return status === 200 ? { ok: true, data, mock: true } : { ok: false, notBuilt: status === 501, data, mock: true };
  }
  try {
    const r = await fetch(`/api/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    rec.status = r.status;
    let data = null;
    try { data = await r.json(); } catch { /* not JSON (e.g. a static file server) */ }
    if (r.status === 501 || data?.error === "not_implemented") return { ok: false, notBuilt: true, data };
    if (!r.ok) return { ok: false, data };
    return { ok: true, data };
  } catch (err) {
    rec.status = "network";
    return { ok: false, offline: true };
  }
}

export function messageFor(res) {
  if (res.ok) return null;
  if (res.notBuilt) return S.toast.notBuilt;
  if (res.offline) return S.toast.offline;
  return S.toast.failed;
}
