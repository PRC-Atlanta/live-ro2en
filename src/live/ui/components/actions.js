// Sends a Live-screen action and says what happened in plain words. A planned endpoint answers
// 501 {error:"not_implemented"} until its session lands: that is shown as a gentle note, never
// an error. Every call is also kept in window.__liveCalls (newest last) for checking by hand.
import { S } from "../i18n/strings.en.js";
import { apiUrl } from "../core/api.js";

// banner/action ids that open a screen instead of calling the backend
const ROUTES = { help: "#help", settings: "#settings", setup: "#setup", after: "#after", names: "#settings" };

export function routeFor(id) { return ROUTES[id] || null; }

// With ?mock there is no backend: mock_state.js answers as the real one would (same shapes,
// refusals and 501s; tests/test_ui_mock_contract.py checks them against contract.json).
const MOCK = typeof location !== "undefined" && new URLSearchParams(location.search).has("mock");
async function mockAnswer(action, body) {
  const { mockApi } = await import("../mock_state.js");
  const r = await mockApi("POST", action, body);
  return [r.status, r.data];
}

export async function send(action, body = {}) {
  const rec = { action, body, status: null, t: Date.now() };
  (window.__liveCalls ||= []).push(rec);
  if (MOCK) {
    const [status, data] = await mockAnswer(action, body);
    rec.status = status;
    return status === 200 ? { ok: true, data, mock: true } : { ok: false, notBuilt: status === 501, status, data, mock: true };
  }
  try {
    const r = await fetch(apiUrl(action), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    rec.status = r.status;
    let data = null;
    try { data = await r.json(); } catch { /* not JSON (e.g. a static file server) */ }
    if (r.status === 501 || data?.error === "not_implemented") return { ok: false, notBuilt: true, data };
    if (!r.ok) return { ok: false, status: r.status, data };
    return { ok: true, data };
  } catch (err) {
    rec.status = "network";
    return { ok: false, offline: true };
  }
}

export function messageFor(res, action) {
  if (res.ok) return null;
  if (res.notBuilt) return S.toast.notBuilt;
  if (res.data?.error === "nothing_to_change") return action === "retract" ? S.toast.nothingToRemove : S.toast.nothingToFix;
  // a refusal the backend words for the operator (a full sentence), e.g. "Stop captioning before …"
  const d = res.data?.detail;
  if ([404, 409, 507].includes(res.status) && typeof d === "string" && /^[A-Z].*\.$/.test(d)) return d;
  if (res.offline) return S.toast.offline;
  return S.toast.failed;
}
