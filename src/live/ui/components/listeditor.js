// List editor for Names & words (one entry per row) and Preferred spellings (pairs:
// "heard as" → "write as"). Rows are plain inputs; onChange(values) fires after each edit,
// remove or add, so the caller can save. Used by screens/settings (S5b).
//
//   const ed = listEditor({ label: "Names & words", values: ["Ionuț Pop"], onChange });
//   const sp = listEditor({ label: "Preferred spellings", pair: true, values: [["Iisus", "Isus"]],
//                           placeholders: ["Heard as", "Write as"], onChange });
//   ed.values() -> ["Ionuț Pop"];   sp.values() -> [["Iisus", "Isus"]]  (blank rows dropped)
export function listEditor({ label, values = [], pair = false, placeholders = [], addLabel = "Add", onChange = () => {} }) {
  const el = document.createElement("div");
  el.className = "fx-list";
  el.setAttribute("role", "group");
  el.setAttribute("aria-label", label);
  const rows = document.createElement("div");
  rows.className = "fx-list";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "fx-btn";
  add.textContent = `+ ${addLabel}`;
  el.append(rows, add);

  const changed = () => onChange(api.values());
  const input = (value, ph, n) => {
    const i = document.createElement("input");
    i.type = "text";
    i.value = value ?? "";
    i.placeholder = ph || "";
    i.spellcheck = false;
    i.setAttribute("aria-label", `${label} ${ph || ""} ${n}`.trim());
    i.addEventListener("change", changed);
    return i;
  };
  const row = (v = pair ? ["", ""] : "") => {
    const r = document.createElement("div");
    r.className = pair ? "fx-item pair" : "fx-item";
    const n = rows.children.length + 1;
    if (pair) {
      const arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = "→";
      arrow.setAttribute("aria-hidden", "true");
      r.append(input(v[0], placeholders[0], n), arrow, input(v[1], placeholders[1], n));
    } else r.append(input(v, placeholders[0], n));
    const x = document.createElement("button");
    x.type = "button";
    x.className = "fx-x";
    x.textContent = "×";
    x.title = "Remove";
    x.setAttribute("aria-label", "Remove");
    x.addEventListener("click", () => { r.remove(); changed(); });
    r.append(x);
    rows.append(r);
    return r;
  };
  add.addEventListener("click", () => row().querySelector("input").focus());

  const api = {
    el,
    values() {
      return [...rows.children].map(r => [...r.querySelectorAll("input")].map(i => i.value.trim()))
        .filter(v => (pair ? v[0] && v[1] : v[0]))
        .map(v => (pair ? v : v[0]));
    },
    set(vals) { rows.replaceChildren(); (vals || []).forEach(v => row(v)); if (!rows.children.length) row(); },
  };
  api.set(values);
  return api;
}
