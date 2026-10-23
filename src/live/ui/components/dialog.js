// Small modal boxes for "Fix last caption" (E) and "Today's names & words" (T).
// ask({title, hint, value, multiline, check, ok, cancel}) -> Promise<{text, checked} | null>
import { ensureStyles } from "./frame.js";

export function ask({ title, hint = "", value = "", multiline = false, check = null, ok = "OK", cancel = "Cancel" }) {
  ensureStyles();
  return new Promise(resolve => {
    const d = document.createElement("dialog");
    d.className = "ltdlg";
    d.innerHTML = `<form method="dialog">
        <h3></h3><p class="sub"></p>
        ${multiline ? `<textarea rows="6"></textarea>` : `<input type="text">`}
        ${check ? `<label class="chk"><input type="checkbox"> <span></span></label>` : ""}
        <div class="row"><button value="cancel" class="btn"></button><button value="ok" class="btn primary"></button></div>
      </form>`;
    d.querySelector("h3").textContent = title;
    d.querySelector("p").textContent = hint;
    const field = d.querySelector("textarea, input[type=text]");
    field.value = value;
    if (check) d.querySelector(".chk span").textContent = check;
    const [bc, bo] = d.querySelectorAll(".row button");
    bc.textContent = cancel; bo.textContent = ok;
    field.addEventListener("keydown", e => {
      if (e.key === "Enter" && (!multiline || e.metaKey)) { e.preventDefault(); d.close("ok"); }
    });
    d.addEventListener("close", () => {
      const res = d.returnValue === "ok"
        ? { text: field.value, checked: !!d.querySelector(".chk input")?.checked } : null;
      d.remove();
      resolve(res);
    });
    document.body.append(d);
    d.showModal();
    field.focus(); field.select?.();
  });
}
