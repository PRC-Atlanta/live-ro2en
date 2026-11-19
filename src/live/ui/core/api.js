import { S } from "../i18n/strings.en.js";

// Backend calls. Endpoints and payloads: contract.json -> endpoints (actions are posted by
// components/actions.js send()).
// Paths are relative to the page (ui/ sits next to api/ and ws): the same files also run as
// Live Remote's full view under /remote/app/ on the captions Mac's network server.
export const apiUrl = path => new URL(`../api/${path}`, location.href).href;

// Live Remote's full view: the Live screen only, without the toolbar strip (remote.py app_view)
export const REMOTE = typeof location !== "undefined" && location.pathname.startsWith("/remote/app/");

export async function get(path) {
  return (await fetch(apiUrl(path))).json();
}

// Close codes that retrying cannot fix: Live Remote's PIN changed (4401), Live Remote was turned
// off (4404) or this address is locked out (4429), or the page is not one of ours (1008).
// Retrying every second would only count more wrong PINs, so the page stops and says so.
const FINAL_CLOSE = new Set([4401, 4404, 4429, 1008]);

// Keep a /ws connection open, writing every state into the store; retry every 1 s.
export function connect(store, onConn = () => {}) {
  const url = new URL("../ws", location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const open = () => {
    const ws = new WebSocket(url.href);
    ws.onopen = () => onConn(true);
    ws.onmessage = e => store.set(JSON.parse(e.data));
    ws.onclose = e => {
      onConn(false);
      if (!FINAL_CLOSE.has(e.code)) { setTimeout(open, 1000); return; }
      const conn = typeof document !== "undefined" && document.getElementById("conn");
      if (conn) { conn.textContent = S.connClosed; conn.hidden = false; }   // not "Trying again…"
    };
  };
  open();
}
