// Station autocomplete bound to an <input>. Calls onPick(station) on selection.
import { api } from "../core/api.js";
import { state } from "../core/store.js";
import { escapeHtml } from "../core/format.js";
import { lineBadge } from "./badge.js";

export function attachAutocomplete(input, onPick) {
  const field = input.closest(".field");
  let box = null, items = [], sel = 0, ctrl = null, timer = 0;

  const close = () => { box?.remove(); box = null; items = []; };
  const render = () => {
    if (!items.length) return close();
    if (!box) { box = document.createElement("div"); box.className = "ac"; field.appendChild(box); }
    box.innerHTML = items.map((s, i) => `
      <div class="ac-item${i === sel ? " sel" : ""}" data-i="${i}">
        <span>${escapeHtml(s.name)}</span>
        <span class="lines">${s.lines.slice(0, 5).map((l) => lineBadge(l)).join("")}</span>
      </div>`).join("");
    box.querySelectorAll(".ac-item").forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); choose(+el.dataset.i); }));
  };
  const choose = (i) => { const s = items[i]; if (!s) return; input.value = s.name; close(); onPick(s); };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) return close();
    timer = setTimeout(async () => {
      ctrl?.abort();
      ctrl = new AbortController();
      try {
        items = await api.search(state.provider.id, q, ctrl.signal);
        sel = 0;
        render();
      } catch (e) { if (e.name !== "AbortError") close(); }
    }, 110);
  });
  input.addEventListener("keydown", (e) => {
    if (!box) return;
    if (e.key === "ArrowDown") { sel = (sel + 1) % items.length; render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = (sel - 1 + items.length) % items.length; render(); e.preventDefault(); }
    else if (e.key === "Enter") { choose(sel); e.preventDefault(); }
    else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
}
