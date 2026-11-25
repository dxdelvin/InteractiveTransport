// One search box for everything: a stop ("Uni"), a line ("Line 4", "4"), or a question
// ("next tram to Göggingen"). Stops open their live board, lines light up on the map,
// anything else goes to the assistant.
import { api } from "../core/api.js";
import { state, emit } from "../core/store.js";
import { escapeHtml } from "../core/format.js";
import { lineBadge } from "./badge.js";

const ASK_ICON = `<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>`;
const ROUTE_ICON = `<svg viewBox="0 0 24 24"><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/></svg>`;

export function initSearch(mapView) {
  const form = document.getElementById("omni");
  const input = document.getElementById("omni-input");
  const list = document.getElementById("omni-list");
  const clear = document.getElementById("omni-clear");
  let items = [], sel = 0, ctrl = null, timer = 0;

  function matchLines(q) {
    const m = q.trim().match(/^(?:line|linie|tram|bus|route)?\s*([a-z0-9]+)$/i);
    const out = [];
    for (const l of state.lines.values()) {
      if (m && l.short_name.toLowerCase() === m[1].toLowerCase()) out.unshift(l);
      else if (q.length >= 3 && l.name.toLowerCase().includes(q.toLowerCase())) out.push(l);
    }
    return out.slice(0, 3);
  }

  async function suggest() {
    const q = input.value.trim();
    clear.hidden = !q;
    if (!q) return close();
    const lines = matchLines(q);
    ctrl?.abort();
    ctrl = new AbortController();
    let stops = [];
    try { stops = await api.search(state.provider.id, q, ctrl.signal); } catch (e) { if (e.name === "AbortError") return; }
    const words = q.split(/\s+/).length;
    items = [
      ...lines.map((l) => ({ kind: "line", l })),
      ...stops.slice(0, 5).map((s) => ({ kind: "stop", s })),
      ...(stops[0] ? [{ kind: "route", s: stops[0] }] : []),
      { kind: "ask", q },
    ];
    // a sentence is a question; a word or two is a name
    sel = words >= 3 && !lines.length ? items.length - 1 : 0;
    render();
  }

  function render() {
    list.hidden = false;
    list.innerHTML = items.map((it, i) => {
      const cls = `opt${i === sel ? " sel" : ""}`;
      if (it.kind === "line") {
        const l = it.l, a = state.stations.get(l.stations[0])?.name, b = state.stations.get(l.stations[l.stations.length - 1])?.name;
        return `<button type="button" class="${cls}" data-i="${i}" role="option">${lineBadge(l.id)}<span class="opt-main"><b>${escapeHtml(l.name)}</b><small>${escapeHtml(a)}${l.loop ? ", circle" : ` to ${escapeHtml(b)}`}</small></span></button>`;
      }
      if (it.kind === "stop") {
        return `<button type="button" class="${cls}" data-i="${i}" role="option"><span class="opt-main"><b>${escapeHtml(it.s.name)}</b><small>Departures</small></span><span class="lines">${it.s.lines.slice(0, 5).map((l) => lineBadge(l)).join("")}</span></button>`;
      }
      if (it.kind === "route") {
        return `<button type="button" class="${cls}" data-i="${i}" role="option"><span class="icon">${ROUTE_ICON}</span><span class="opt-main"><b>Route to ${escapeHtml(it.s.name)}</b><small>From ${state.userLocation ? "your location" : escapeHtml(state.anchor?.label || "here")}</small></span></button>`;
      }
      return `<button type="button" class="${cls}" data-i="${i}" role="option"><span class="icon">${ASK_ICON}</span><span class="opt-main"><b>Ask: ${escapeHtml(it.q)}</b><small>Trips, next departures, delays</small></span></button>`;
    }).join("");
    list.querySelectorAll("[data-i]").forEach((b) => b.addEventListener("mousedown", (e) => { e.preventDefault(); choose(+b.dataset.i); }));
  }

  function close() { list.hidden = true; items = []; }

  function choose(i) {
    const it = items[i];
    if (!it) return;
    close();
    input.blur();
    if (it.kind === "line") {
      input.value = "";
      clear.hidden = true;
      emit("showLine", it.l.id);
    } else if (it.kind === "stop") {
      input.value = "";
      clear.hidden = true;
      emit("select", { kind: "station", id: it.s.id });
    } else if (it.kind === "route") {
      input.value = "";
      clear.hidden = true;
      const a = state.anchor;
      const origin = state.userLocation ? { lat: state.userLocation.lat, lon: state.userLocation.lon, label: "Your location" } : a ? { lat: a.lat, lon: a.lon, label: a.label } : null;
      emit("planTrip", { origin, originLabel: state.userLocation ? "Your location" : a?.label || "", destination: { station_id: it.s.id }, destinationLabel: it.s.name });
    } else {
      input.value = "";
      clear.hidden = true;
      emit("ask", it.q);
    }
  }

  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(suggest, 120); });
  input.addEventListener("focus", () => { if (input.value.trim()) suggest(); emit("sheetFull", true); });
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { sel = (sel + 1) % items.length; render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = (sel - 1 + items.length) % items.length; render(); e.preventDefault(); }
    else if (e.key === "Escape") close();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    if (list.hidden || !items.length) await suggest();
    choose(sel);
  });
  clear.addEventListener("click", () => { input.value = ""; clear.hidden = true; close(); input.focus(); });
}
