// The stop display: departures for a stop, or the run of a single tram/bus.
// Styled after the LED boards at swa stops - the one loud element in the interface.
import { api } from "../core/api.js";
import { state, emit, on } from "../core/store.js";
import { escapeHtml, eta } from "../core/format.js";
import { lineBadge } from "./badge.js";

const CLOSE = `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>`;
const LOAD = { low: "Seats free", medium: "Busy", high: "Full", unknown: "Unknown" };

export function initInspector(mapView) {
  const el = document.getElementById("inspector");
  let timer = 0, tick = 0, ctrl = null, fetchedAt = 0, current = null;

  on("select", async (sel) => {
    clearInterval(timer);
    clearInterval(tick);
    ctrl?.abort();
    current = sel;
    if (!sel) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<div class="board-head"><span></span><div class="board-sub">Loading</div><span></span></div>`;
    await load();
    timer = setInterval(load, sel.kind === "vehicle" ? 3000 : 6000);
    tick = setInterval(countdown, 1000);
  });
  on("follow", () => current?.kind === "vehicle" && renderFollow());

  async function load() {
    const sel = current;
    if (!sel) return;
    ctrl = new AbortController();
    try {
      const data = sel.kind === "vehicle" ? await api.vehicle(state.provider.id, sel.id, ctrl.signal) : await api.station(state.provider.id, sel.id, ctrl.signal);
      if (sel !== current) return;
      fetchedAt = Date.now();
      sel.kind === "vehicle" ? renderVehicle(data) : renderStation(data);
    } catch (e) {
      if (e.name === "AbortError") return;
      if (e.status === 404 && sel.kind === "vehicle") {
        emit("toast", { text: "That vehicle has finished its run." });
        emit("select", null);
      }
    }
  }

  function countdown() {
    const elapsed = (Date.now() - fetchedAt) / 1000;
    el.querySelectorAll("[data-eta]").forEach((n) => {
      const s = Math.max(0, +n.dataset.eta - elapsed);
      n.textContent = eta(s);
      n.classList.toggle("now", s < 30);
    });
  }

  function bindClose() {
    el.querySelector("[data-close]").onclick = () => emit("select", null);
  }

  function renderStation(d) {
    const s = d.station;
    el.innerHTML = `
      <div class="board-head">
        <span class="h-sign" aria-hidden="true">H</span>
        <div>
          <div class="board-title">${escapeHtml(s.name)}</div>
          <div class="board-sub">${s.lines.map((id) => state.lines.get(id)?.name || id).join(", ")}</div>
        </div>
        <button class="board-close" data-close aria-label="Close">${CLOSE}</button>
      </div>
      <ul class="rows">
        ${d.arrivals.map((a) => `
          <li class="row" ${a.vehicle_id ? `data-vehicle="${escapeHtml(a.vehicle_id)}"` : ""}>
            ${lineBadge(a.line_id, a.line_name)}
            <span class="dest">${escapeHtml(a.headsign)}</span>
            <span><span class="eta" data-eta="${a.eta_s}">${eta(a.eta_s)}</span>${a.delay_s >= 60 ? `<span class="late">${Math.round(a.delay_s / 60)} min late</span>` : ""}</span>
          </li>`).join("") || `<li class="empty">No departures in the next hour.</li>`}
      </ul>
      <div class="source">${d.source === "live" ? "<b>Live</b> times from bahnland-bayern.de" : "Estimated from the timetable. Live times aren't available right now."}</div>
      <div class="board-foot">
        <button data-from>Route from here</button>
        <button data-to>Route to here</button>
      </div>`;
    bindClose();
    el.querySelector("[data-from]").onclick = () => emit("planFrom", s);
    el.querySelector("[data-to]").onclick = () => emit("planTo", s);
    el.querySelectorAll("[data-vehicle]").forEach((li) => (li.onclick = () => {
      emit("select", { kind: "vehicle", id: li.dataset.vehicle });
      mapView.flyToVehicle(li.dataset.vehicle);
    }));
    countdown();
  }

  function renderVehicle(d) {
    const v = d.vehicle;
    const line = state.lines.get(v.line_id);
    const kind = line?.mode === "bus" ? "Bus" : line?.mode === "tram" ? "Tram" : "Train";
    const late = v.delay_s >= 60;
    const where = v.status === "stopped" ? `At ${escapeHtml(state.stations.get(v.current_station_id)?.name || "a stop")}` : `Next stop ${escapeHtml(d.next_station_name || "unknown")}`;
    el.innerHTML = `
      <div class="board-head">
        ${lineBadge(v.line_id)}
        <div>
          <div class="board-title">To ${escapeHtml(d.destination_name)}</div>
          <div class="board-sub">${kind} ${escapeHtml(v.label)}. ${where}</div>
        </div>
        <button class="board-close" data-close aria-label="Close">${CLOSE}</button>
      </div>
      <dl class="vstats">
        <div><dt>Speed</dt><dd>${Math.round(v.speed_kmh)} km/h</dd></div>
        <div><dt>Timing${v.delay_source === "estimated" ? " (est.)" : ""}</dt><dd class="${late ? "late" : ""}">${late ? `${Math.round(v.delay_s / 60)} min late` : "On time"}</dd></div>
        <div><dt>Load</dt><dd>${LOAD[v.occupancy] || "Unknown"}</dd></div>
      </dl>
      <div class="progress" title="How far along its run"><i style="width:${(d.progress * 100).toFixed(1)}%"></i></div>
      <ul class="rows">
        ${d.upcoming.slice(0, 12).map((u) => `
          <li class="row stop" data-station="${escapeHtml(u.station_id)}">
            <span class="pin"></span><span class="dest">${escapeHtml(u.name)}</span><span class="eta" data-eta="${u.eta_s}">${eta(u.eta_s)}</span>
          </li>`).join("") || `<li class="empty">Arriving at the last stop.</li>`}
      </ul>
      <div class="source">${v.position_source === "timetable"
        ? `Position estimated from the timetable${v.delay_source === "live" ? ", shifted by the line's <b>live</b> delay" : ""}. swa doesn't publish vehicle locations.`
        : "<b>Live</b> GPS position"}</div>
      <div class="board-foot" id="follow-actions"></div>`;
    bindClose();
    el.querySelectorAll("[data-station]").forEach((li) => (li.onclick = () => emit("select", { kind: "station", id: li.dataset.station })));
    renderFollow();
    countdown();
  }

  function renderFollow() {
    const box = el.querySelector("#follow-actions");
    if (!box) return;
    const f = state.follow;
    box.innerHTML = `
      <button data-f="track" class="${f === "track" ? "on" : ""}">${f === "track" ? "Following" : "Follow on map"}</button>
      <button data-line>Show line ${escapeHtml(state.lines.get(state.vehicles.get(current.id)?.line_id)?.short_name || "")}</button>`;
    box.querySelectorAll("[data-f]").forEach((b) => (b.onclick = () => {
      state.follow = state.follow === b.dataset.f ? null : b.dataset.f;
      emit("follow", state.follow);
    }));
    box.querySelector("[data-line]").onclick = () => { const v = state.vehicles.get(current.id); if (v) mapView.fitLines([v.line_id]); };
  }
}
