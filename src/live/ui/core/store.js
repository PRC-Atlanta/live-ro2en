// The one copy of the backend state (contract.json -> state). Screens subscribe; only
// api.js (or mock_state.js) writes it.
export function createStore(initial = {}) {
  let state = initial;
  const subs = new Set();
  return {
    get: () => state,
    set(next) { state = next; subs.forEach(fn => fn(state)); },
    subscribe(fn) { subs.add(fn); fn(state); return () => subs.delete(fn); },
  };
}
