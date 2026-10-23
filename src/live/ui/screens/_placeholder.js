// Shared stand-in until a screen is built: names the screen and shows the raw state.
export function placeholder(title, owner, fields) {
  return (el, store) => {
    el.innerHTML = `<section class="placeholder"><h2>${title}</h2>
      <p>Not built yet (${owner}). State fields this screen uses:</p><pre></pre></section>`;
    const pre = el.querySelector("pre");
    return store.subscribe(s => {
      pre.textContent = JSON.stringify(Object.fromEntries(fields.map(f => [f, s[f]])), null, 2);
    });
  };
}
