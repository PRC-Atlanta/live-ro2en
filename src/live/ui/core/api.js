// Backend calls. Endpoints and payloads: contract.json -> endpoints. A planned endpoint
// answers 501 {error:"not_implemented", endpoint, owner}; post() returns that object.
// Paths are relative to the page (ui/ sits next to api/ and ws): the same files also run as
// Live Remote's full view under /remote/app/ on the captions Mac's network server.
export const apiUrl = path => new URL(`../api/${path}`, location.href).href;

// Live Remote's full view: the Live screen only, without the toolbar strip (remote.py app_view)
export const REMOTE = typeof location !== "undefined" && location.pathname.startsWith("/remote/app/");

export async function post(action, body = {}) {
  const r = await fetch(apiUrl(action), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
}

export async function get(path) {
  return (await fetch(apiUrl(path))).json();
}

// Keep a /ws connection open, writing every state into the store; retry every 1 s.
export function connect(store, onConn = () => {}) {
  const url = new URL("../ws", location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const open = () => {
    const ws = new WebSocket(url.href);
    ws.onopen = () => onConn(true);
    ws.onmessage = e => store.set(JSON.parse(e.data));
    ws.onclose = () => { onConn(false); setTimeout(open, 1000); };
  };
  open();
}
