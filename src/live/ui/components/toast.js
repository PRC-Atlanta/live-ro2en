// A short note at the bottom of the window ("That control isn't ready in this version yet."),
// also read out by screen readers. kind: ok | info | warn.
let box = null, timer = 0;

export function toast(text, kind = "info", ms = 3200) {
  if (!text) return;
  if (!box || !box.isConnected) {
    box = document.createElement("div");
    box.className = "toast"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite");
    document.body.append(box);
  }
  box.textContent = text; box.dataset.kind = kind; box.classList.add("show");
  clearTimeout(timer);
  timer = setTimeout(() => box.classList.remove("show"), ms);
}
