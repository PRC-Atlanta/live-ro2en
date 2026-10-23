// A click-to-select knob: the positions are 44 px buttons around it, and a click on the knob
// itself moves to the next one. Never dragged. knob(el, {id, label, keys, positions:[{value,
// label, at, angle}], face, onPick}) -> set(value).
export function knob(el, { id, label, keys, positions, face, onPick }) {
  el.innerHTML = `
    <div class="knobwrap">
      ${positions.map(p => `<button class="pos" data-v="${p.value}" style="${p.at}"></button>`).join("")}
      <button class="knob" data-knob="${id}" aria-label="${label}"><span class="ptr"></span><span class="val"></span></button>
    </div>
    <div class="label">${label}</div>
    <div class="sub">${keys}</div>`;
  const btns = [...el.querySelectorAll(".pos")];
  btns.forEach((b, i) => { b.textContent = positions[i].label; b.onclick = () => onPick(positions[i].value); });
  const k = el.querySelector(".knob");
  let cur = null;
  k.onclick = () => {
    const i = positions.findIndex(p => p.value === cur);
    onPick(positions[(i + 1) % positions.length].value);
  };
  return function set(value) {
    if (value === cur) return;
    cur = value;
    const p = positions.find(x => x.value === value) || positions[0];
    k.querySelector(".ptr").style.transform = `rotate(${p.angle}deg) translateY(27px)`;
    k.querySelector(".val").textContent = face[value] ?? value;
    k.setAttribute("aria-label", `${label}: ${p.label}`);
    btns.forEach(b => { const on = b.dataset.v === value; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
  };
}
