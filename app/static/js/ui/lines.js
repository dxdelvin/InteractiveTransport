// Lines, grouped into trams and buses, with live service state. Click to focus a line.
import { api } from "../core/api.js";
import { state, emit, on } from "../core/store.js";
import { escapeHtml, minutes } from "../core/format.js";
import { lineBadge } from "./badge.js";

const GROUPS = [["tram", "Trams"], ["light_rail", "Light rail"], ["metro", "Subway"], ["rail", "Trains"], ["bus", "Buses"], ["ferry", "Ferries"]];
const HEALTH = { good: "Running normally", minor_delays: "Minor delays", major_delays: "Major delays", no_service: "Not running now" };

export function initLines(mapView) {
  const box = document.getElementById("line-list");
  let statuses = [];

  async function refresh() {
    if (!state.provider) return;
    try {
      statuses = await api.lineStatus(state.provider.id);
      render();
    } catch { /* next tick retries */ }
  }

  function ends(lineId) {
    const l = state.lines.get(lineId);
    if (!l) return "";
    const a = state.stations.get(l.stations[0])?.name, b = state.stations.get(l.stations[l.stations.length - 1])?.name;
    return l.loop ? `${escapeHtml(a)} <span>circle</span>` : `${escapeHtml(a)} <span>to</span> ${escapeHtml(b)}`;
  }

  function render() {
    const focus = state.focusLines;
    const byMode = new Map();
    for (const s of statuses) {
      if (!byMode.has(s.mode)) byMode.set(s.mode, []);
      byMode.get(s.mode).push(s);
    }
    box.innerHTML = GROUPS.filter(([m]) => byMode.has(m)).map(([m, title]) => `
      <h2 class="group-title">${title}</h2>
      ${byMode.get(m).map((s) => `
        <div class="line-row${focus?.has(s.line_id) ? " focused" : ""}${focus && !focus.has(s.line_id) ? " dim" : ""}" data-id="${escapeHtml(s.line_id)}" role="button" tabindex="0">
          ${lineBadge(s.line_id)}
          <div>
            <div class="line-ends">${ends(s.line_id)}</div>
            <div class="line-state ${s.health}">${HEALTH[s.health]}${s.effective_headway_s && s.health !== "no_service" ? `, every ${Math.max(1, minutes(s.effective_headway_s))} min` : ""}</div>
          </div>
          <div class="line-count"><b>${s.vehicles_active}</b>running</div>
        </div>`).join("")}`).join("") + (focus ? `<button class="lines-reset" id="lines-all">Show every line</button>` : "");

    box.querySelectorAll(".line-row").forEach((el) => {
      const pick = () => {
        const id = el.dataset.id;
        const already = state.focusLines?.size === 1 && state.focusLines.has(id);
        state.focusLines = already ? null : new Set([id]);
        emit("focusLines");
        if (!already) mapView.fitLines([id]);
      };
      el.addEventListener("click", pick);
      el.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), pick()));
    });
    box.querySelector("#lines-all")?.addEventListener("click", () => {
      state.focusLines = null;
      emit("focusLines");
      mapView.resetView();
    });
  }

  on("focusLines", render);
  on("networkLoaded", refresh);
  setInterval(refresh, 10000);
}
