// Backend calls. Endpoints and payloads: contract.json -> endpoints. A planned endpoint
// answers 501 {error:"not_implemented", endpoint, owner}; post() returns that object.
export async function post(action, body = {}) {
  const r = await fetch(`/api/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
}

export async function get(path) {
  return (await fetch(`/api/${path}`)).json();
}

// Keep a /ws connection open, writing every state into the store; retry every 1 s.
export function connect(store, onConn = () => {}) {
  const open = () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => onConn(true);
    ws.onmessage = e => store.set(JSON.parse(e.data));
    ws.onclose = () => { onConn(false); setTimeout(open, 1000); };
  };
  open();
}
