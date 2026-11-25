import { on } from "../core/store.js";

export function initToasts() {
  const box = document.getElementById("toasts");
  on("toast", ({ text, error = false, ms = 4200 }) => {
    const el = document.createElement("div");
    el.className = `toast${error ? " error" : ""}`;
    el.textContent = text;
    box.appendChild(el);
    setTimeout(() => el.remove(), ms);
  });
}
