// Home: what's wrong on the network, and the next departures near you.
//
// The first departure at the closest stop is the loud element (an LED card). Every time
// says where it comes from: "on time" / "+3 min" only when the board is live; scheduled
// estimates are labelled as such. A reminder tells you when to start walking.
import { api } from "../core/api.js";
import { state, emit, on } from "../core/store.js";
import { escapeHtml, eta } from "../core/format.js";
import { haversine } from "../core/geo.js";
import { lineBadge } from "./badge.js";

const WALK_MPS = 1.25;
const NEAR_M = 650;
const DEFAULT_STOP = { augsburg: "Königsplatz" };  // by name: ids differ between data sources
const LIVE = `<svg class="live-mark" viewBox="0 0 24 24" aria-label="live"><path d="M5 19a14 14 0 0 1 14-14M5 12.5A7.5 7.5 0 0 1 12.5 5"/><circle cx="5.5" cy="18.5" r="1.4" fill="currentColor" stroke="none"/></svg>`;
const BELL = `<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>`;

export function initHome(mapView) {
  const alertsBox = document.getElementById("alerts");
  const minorBox = document.getElementById("alerts-minor");
  const box = document.getElementById("nearby");
  let anchor = null;          // { lat, lon, label, user }
  let boards = [];            // [{ station, dist, data, fetchedAt }]
  let alerts = [];
  let reminder = null;        // { key, stationId, stationName, lineId, headsign, etaAbs, walkS, timer }
  let ctrl = null;

  // ------------------------------------------------------------ anchor
  function defaultAnchor() {
    const name = DEFAULT_STOP[state.provider.id];
    let s = name && [...state.stations.values()].find((x) => x.name === name);
    if (!s) {
      const c = state.provider.center;
      s = [...state.stations.values()].sort((a, b) => haversine([a.lon, a.lat], c) - haversine([b.lon, b.lat], c))[0];
    }
    return { lat: s.lat, lon: s.lon, label: s.name, user: false };
  }

  function setAnchor(a, { fly = true } = {}) {
    anchor = a;
    state.anchor = a;
    if (fly) mapView.flyTo(a.lon, a.lat, 15.3, !boards.length);
    refresh();
  }

  function useLocation(silent = false) {
    if (!navigator.geolocation) { if (!silent) emit("toast", { text: "This browser can't share your location. Showing departures at the city centre." }); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude: lat, longitude: lon } = pos.coords;
        const [w, s, e, n] = state.network.bbox;
        if (!(lon > w - 0.03 && lon < e + 0.03 && lat > s - 0.03 && lat < n + 0.03)) {
          const km = Math.round(haversine([lon, lat], state.provider.center) / 1000);
          if (!silent) emit("toast", { text: `You're about ${km} km from ${state.provider.city}, so this shows the city centre instead.` });
          return;
        }
        state.userLocation = { lat, lon };
        mapView.setUserLocation(lat, lon);
        setAnchor({ lat, lon, label: "you", user: true });
      },
      () => { if (!silent) emit("toast", { text: "Location is blocked for this site. Allow it in the browser to see stops near you." }); },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 },
    );
  }
  on("locate", () => useLocation(false));

  // ------------------------------------------------------------ data
  function nearestStations() {
    const here = [anchor.lon, anchor.lat];
    const all = [...state.stations.values()].map((s) => ({ s, d: haversine(here, [s.lon, s.lat]) })).sort((a, b) => a.d - b.d);
    const near = all.filter((x) => x.d <= NEAR_M).slice(0, 2);
    return near.length ? near : all.slice(0, 1);
  }

  async function refresh() {
    if (!anchor || !state.provider) return;
    ctrl?.abort();
    ctrl = new AbortController();
    const sig = ctrl.signal;
    const picks = nearestStations();
    try {
      const datas = await Promise.all(picks.map((p) => api.station(state.provider.id, p.s.id, sig, 8)));
      boards = picks.map((p, i) => ({ station: p.s, dist: p.d, data: datas[i], fetchedAt: Date.now() }));
      render();
      trackReminder();
    } catch (e) {
      if (e.name !== "AbortError" && !boards.length) box.innerHTML = `<p class="muted">Departures didn't load (${escapeHtml(e.message)}). They'll retry in a few seconds.</p>`;
    }
  }

  async function refreshAlerts() {
    if (!state.provider) return;
    try {
      alerts = await api.alerts(state.provider.id);
      emit("alerts", alerts);
      renderAlerts();
    } catch { /* keep the last ones */ }
  }

  // ------------------------------------------------------------ alerts
  function delaySource() {
    const vs = [...state.vehicles.values()];
    return vs.some((v) => v.delay_source === "live") ? "live" : vs.some((v) => v.delay_source === "estimated") ? "estimated" : "none";
  }

  function renderAlerts() {
    const late = alerts.filter((a) => a.severity !== "no_service");
    const idle = alerts.filter((a) => a.severity === "no_service");
    const src = delaySource();
    const srcTag = (s) => (s === "live" ? `<span class="src live">live</span>` : s === "estimated" ? `<span class="src">estimated</span>` : "");
    const major = late.filter((a) => a.severity === "major");
    const minor = late.filter((a) => a.severity === "minor");
    let html = major.map((a) => `
      <div class="alert major" data-line="${escapeHtml(a.line_id)}" role="button" tabindex="0">
        ${lineBadge(a.line_id)}
        <span class="alert-text">${escapeHtml(a.text)}<small>Long delay ${srcTag(a.source)}</small></span>
        <button class="alert-act" data-alt="${escapeHtml(a.line_id)}">Find another way</button>
      </div>`).join("");
    let low = "";
    if (minor.length) {
      low += `<div class="minor-line"><span>A few minutes late ${srcTag(minor[0].source)}</span>${minor.map((a) => `<button class="minor-chip" data-line="${escapeHtml(a.line_id)}" title="${escapeHtml(a.text)}">${lineBadge(a.line_id)}+${Math.round(a.delay_s / 60)}</button>`).join("")}</div>`;
    }
    if (!late.length && state.vehicles.size) {
      low += `<div class="all-ok"><i></i>All running lines are on time ${src === "estimated" ? `<span class="src">estimated, no live data right now</span>` : srcTag(src)}</div>`;
    }
    if (idle.length) low += `<div class="idle">Not running now: ${idle.map((a) => lineBadge(a.line_id)).join(" ")}</div>`;
    alertsBox.innerHTML = html;
    minorBox.innerHTML = low;
    document.querySelectorAll("#alerts .alert, #alerts-minor .minor-chip").forEach((el) => {
      const focus = () => { state.focusLines = new Set([el.dataset.line]); emit("focusLines"); mapView.fitLines([el.dataset.line]); };
      el.addEventListener("click", (e) => { if (!e.target.closest("[data-alt]")) focus(); });
      el.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), focus()));
    });
    alertsBox.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => findAlternative(b.dataset.alt)));
  }

  /** Route around a delayed line: from here to where that line would have taken you. */
  function findAlternative(lineId) {
    const line = state.lines.get(lineId);
    if (!line) return;
    const near = boards[0]?.station;
    const dep = boards.flatMap((b) => b.data.arrivals).find((a) => a.line_id === lineId);
    const destName = dep?.headsign || state.stations.get(line.stations[line.stations.length - 1])?.name;
    const origin = anchor?.user ? { lat: anchor.lat, lon: anchor.lon, label: "Your location" } : near ? { station_id: near.id } : null;
    if (!origin || !destName) return;
    emit("planAlternative", { origin, originLabel: anchor?.user ? "Your location" : near.name, destination: { query: destName }, destinationLabel: destName, avoid: [lineId] });
  }

  // ------------------------------------------------------------ departures
  function modeWord(lineId) {
    const m = state.lines.get(lineId)?.mode;
    return m === "bus" ? "Bus" : m === "tram" ? "Tram" : "Line";
  }

  const walkS = (dist) => (anchor?.user ? Math.round((dist * 1.25) / WALK_MPS) : 0);
  const keyOf = (stationId, a) => `${stationId}|${a.line_id}|${a.headsign}`;

  /** Departures grouped by line and destination: one card per way you could go. */
  function groups() {
    const out = new Map();
    boards.forEach((b, bi) => {
      const live = b.data.source === "live";
      for (const a of b.data.arrivals) {
        const k = `${a.line_id}|${a.headsign}`;
        if (!out.has(k)) out.set(k, { key: k, board: b, bi, live, deps: [] });
        const g = out.get(k);
        if (g.board === b && g.deps.length < 3) g.deps.push(a);
      }
    });
    return [...out.values()].sort((x, y) => x.deps[0].eta_s - y.deps[0].eta_s).slice(0, 7);
  }

  function render() {
    if (!boards.length) return;
    const head = `<div class="near-head"><h2>Near <b>${escapeHtml(anchor.user ? "you" : anchor.label)}</b></h2>${anchor.user ? "" : `<button class="link" data-locate>Use my location</button>`}</div>`;
    const stops = `<div class="stop-chips">${boards.map((b) => {
      const w = walkS(b.dist);
      return `<button class="chip" data-station="${escapeHtml(b.station.id)}"><span class="h-dot" aria-hidden="true">H</span>${escapeHtml(b.station.name)}${w ? `<small>${Math.max(1, Math.round(w / 60))} min walk</small>` : ""}</button>`;
    }).join("")}</div>`;
    const cards = groups().map((g) => {
      const a = g.deps[0];
      const line = state.lines.get(a.line_id);
      const color = line?.color || a.color || "#5a6670";
      const text = line?.text_color || "#ffffff";
      const k = keyOf(g.board.station.id, a);
      const on = reminder?.key === k;
      const lateMins = g.live && a.delay_s >= 60 ? Math.round(a.delay_s / 60) : 0;
      const sev = lateMins >= 5 ? "major" : lateMins ? "minor" : "";
      const disrupted = alerts.find((x) => x.line_id === a.line_id && x.severity === "major");
      const st = g.live ? (lateMins ? `${lateMins} min late` : "On time") : "Scheduled, not live";
      return `
        <article class="rcard${on ? " reminding" : ""}" style="--c:${escapeHtml(color)};--t:${escapeHtml(text)}" ${a.vehicle_id ? `data-vehicle="${escapeHtml(a.vehicle_id)}"` : ""}>
          <div class="rc-main">
            <div class="rc-id"><span class="rc-num${line?.mode === "bus" ? " bus" : ""}">${escapeHtml(line?.short_name || a.line_name)}</span><span class="rc-mode">${modeWord(a.line_id)}</span></div>
            <div class="rc-dest">${escapeHtml(a.headsign)}</div>
            <div class="rc-stop">from ${escapeHtml(g.board.station.name)}</div>
          </div>
          <div class="rc-etas">
            ${g.deps.map((d, i) => `<span class="rc-eta${i === 0 ? " first" : ""}">${g.live || d.eta_s < 60 ? "" : `<i class="tilde" title="Scheduled estimate">~</i>`}<span data-eta="${d.eta_s}" data-min>${minOnly(d.eta_s)}</span>${i === 0 ? `<small data-unit>${d.eta_s < 60 ? "" : "min"}</small>${g.live ? LIVE : ""}` : ""}</span>`).join("")}
          </div>
          <div class="rc-foot">
            <span class="rc-status ${sev}">${st}${disrupted ? ` <button class="rc-alt" data-alt="${escapeHtml(a.line_id)}">Find another way</button>` : ""}</span>
            <button class="remind${on ? " on" : ""}" data-remind="${escapeHtml(k)}" aria-label="Remind me when to leave for this ${modeWord(a.line_id).toLowerCase()}">${BELL}${on ? `Leave at ${clockAt(reminder.etaAbs - reminder.walkS * 1000 - 60000)}` : "Remind me"}</button>
          </div>
        </article>`;
    }).join("") || `<p class="muted">No departures nearby in the next hour.</p>`;
    const anyEst = boards.some((b) => b.data.source !== "live");
    const legend = `<p class="legend">${anyEst ? `<span><i class="tilde">~</i> scheduled estimate</span>` : ""}${boards.some((b) => b.data.source === "live") ? `<span>${LIVE} live from bahnland-bayern.de</span>` : ""}</p>`;
    box.innerHTML = head + stops + `<div class="rcards">${cards}</div>` + legend;
    box.dataset.fetched = String(boards[0].fetchedAt);

    box.querySelector("[data-locate]")?.addEventListener("click", () => useLocation(false));
    box.querySelectorAll("[data-station]").forEach((b) => b.addEventListener("click", () => emit("select", { kind: "station", id: b.dataset.station })));
    box.querySelectorAll(".rcard[data-vehicle]").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      emit("select", { kind: "vehicle", id: el.dataset.vehicle });
      mapView.flyToVehicle(el.dataset.vehicle);
    }));
    box.querySelectorAll("[data-remind]").forEach((b) => b.addEventListener("click", () => {
      const g = groups().find((x) => keyOf(x.board.station.id, x.deps[0]) === b.dataset.remind);
      if (g) toggleReminder(g.board, g.deps[0]);
    }));
    box.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => findAlternative(b.dataset.alt)));
    tickCountdowns();
  }

  function minOnly(s) {
    return s < 60 ? "now" : String(Math.round(s / 60));
  }

  function tickCountdowns() {
    const elapsed = (Date.now() - +(box.dataset.fetched || Date.now())) / 1000;
    box.querySelectorAll("[data-eta]").forEach((n) => {
      const s = Math.max(0, +n.dataset.eta - elapsed);
      n.textContent = n.hasAttribute("data-min") ? minOnly(s) : eta(s);
      const unit = n.parentElement.querySelector("[data-unit]");
      if (unit) unit.textContent = s < 60 ? "" : "min";
    });
  }

  // ------------------------------------------------------------ leave-now reminder
  const clockAt = (abs) => new Date(abs).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

  async function toggleReminder(board, dep) {
    const k = keyOf(board.station.id, dep);
    if (reminder?.key === k) { clearTimeout(reminder.timer); reminder = null; render(); return; }
    if ("Notification" in window && Notification.permission === "default") {
      try { await Notification.requestPermission(); } catch { /* the in-page toast still works */ }
    }
    clearTimeout(reminder?.timer);
    reminder = {
      key: k, stationId: board.station.id, stationName: board.station.name, lineId: dep.line_id, headsign: dep.headsign,
      etaAbs: board.fetchedAt + dep.eta_s * 1000, walkS: walkS(board.dist), timer: 0,
    };
    schedule();
    render();
    const leaveAt = reminder.etaAbs - reminder.walkS * 1000 - 60000;
    emit("toast", { text: leaveAt <= Date.now() ? `Leave now to catch it at ${clockAt(reminder.etaAbs)}.` : `We'll tell you at ${clockAt(leaveAt)} to walk to ${board.station.name}. Keep this tab open.` });
  }

  /** Follow the departure through refreshes, so a delay moves the reminder too. */
  function trackReminder() {
    if (!reminder) return;
    const b = boards.find((x) => x.station.id === reminder.stationId);
    if (!b) return;
    const match = b.data.arrivals
      .filter((a) => a.line_id === reminder.lineId && a.headsign === reminder.headsign)
      .map((a) => ({ a, abs: b.fetchedAt + a.eta_s * 1000 }))
      .sort((x, y) => Math.abs(x.abs - reminder.etaAbs) - Math.abs(y.abs - reminder.etaAbs))[0];
    if (match && Math.abs(match.abs - reminder.etaAbs) < 10 * 60000) {
      reminder.etaAbs = match.abs;
      schedule();
    }
  }

  function schedule() {
    clearTimeout(reminder.timer);
    const r = reminder;
    const fireIn = r.etaAbs - r.walkS * 1000 - 60000 - Date.now();
    if (fireIn <= 0) return;
    r.timer = setTimeout(() => {
      const line = state.lines.get(r.lineId);
      const text = `Walk to ${r.stationName} now to catch the ${modeWord(r.lineId).toLowerCase()} ${line?.short_name || ""} at ${clockAt(r.etaAbs)}.`;
      emit("toast", { text, ms: 15000 });
      if ("Notification" in window && Notification.permission === "granted") {
        try { new Notification("Time to go", { body: text, tag: "leave-now" }); } catch { /* some browsers need a service worker */ }
      }
      reminder = null;
      render();
    }, Math.min(fireIn, 2 ** 31 - 1));
  }

  // ------------------------------------------------------------ lifecycle
  on("networkLoaded", async () => {
    boards = [];
    alerts = [];
    if (reminder) { clearTimeout(reminder.timer); reminder = null; }
    box.innerHTML = `<p class="muted">Finding departures near you</p>`;
    setAnchor(defaultAnchor());
    refreshAlerts();
    // only ask for location silently if the visitor already allowed it once
    try {
      const p = await navigator.permissions?.query({ name: "geolocation" });
      if (p?.state === "granted") useLocation(true);
    } catch { /* permissions API not available */ }
  });
  on("vehiclesUpdated", () => alerts.length || renderAlerts());
  setInterval(refresh, 20000);
  setInterval(refreshAlerts, 20000);
  setInterval(tickCountdowns, 1000);
}
