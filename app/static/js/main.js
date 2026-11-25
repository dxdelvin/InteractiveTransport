// App bootstrap: wires the map, realtime feed and panel together.
import { api } from "./core/api.js";
import { state, emit, on } from "./core/store.js";
import { LineGeometry } from "./core/geo.js";
import { Realtime } from "./core/realtime.js";
import { MapView } from "./map/mapView.js";
import { initToasts } from "./ui/toast.js";
import { initPlanner } from "./ui/planner.js";
import { initLines } from "./ui/lines.js";
import { initAssistant } from "./ui/assistant.js";
import { initInspector } from "./ui/inspector.js";
import { initHome } from "./ui/home.js";
import { initSearch } from "./ui/search.js";

const $ = (id) => document.getElementById(id);

initToasts();
const mapView = new MapView("map");
window.__transit = { state, mapView, emit }; // handy for debugging in the console
initPlanner(mapView);
initLines(mapView);
initAssistant();
initInspector(mapView);
initHome(mapView);
initSearch(mapView);

// ---------------------------------------------------------------- realtime + honesty line
const connState = { status: "connecting", count: 0 };
function paintConn(detail) {
  const { status, count } = connState;
  const ok = status === "live" || status === "polling";
  const estimated = state.provider?.kind === "simulated";
  const noun = state.provider?.id === "augsburg" ? "trams and buses" : "vehicles";
  $("hud-conn-label").textContent = ok
    ? `${count} ${noun}${estimated ? "" : " tracked live"}`
    : status === "error" ? "Positions paused" : status === "reconnecting" ? "Reconnecting" : "Connecting";
  $("hud-conn").className = `conn ${ok ? (estimated ? "est" : "ok") : status === "error" ? "bad" : ""}`;
  $("hud-conn").title = detail || "";
  const liveDelays = [...state.vehicles.values()].some((v) => v.delay_source === "live");
  $("data-note").textContent = !ok ? "" : estimated
    ? (liveDelays ? "Estimated positions, live delays" : "Estimated positions from the timetable")
    : "GPS positions from the operator";
}
const realtime = new Realtime({
  onSnapshot(snap) {
    state.vehicles = new Map(snap.vehicles.map((v) => [v.id, v]));
    mapView.updateVehicles(snap.vehicles);
    connState.count = snap.vehicles.length;
    paintConn();
    emit("vehiclesUpdated");
  },
  onStatus(status, detail) {
    connState.status = status;
    paintConn(detail);
    if (status === "error" && detail) emit("toast", { text: `Vehicle positions stopped updating: ${detail}`, error: true });
  },
});

// ---------------------------------------------------------------- provider switching
async function loadProvider(id) {
  const info = state.providers.find((p) => p.id === id) || state.providers[0];
  const loader = $("loader");
  loader.classList.remove("done");
  loader.querySelector(".loader-word").textContent = info.city;
  loader.querySelector(".loader-text").textContent = info.kind === "live" ? `Connecting to the ${info.city} live feed` : "Loading trams and buses";
  emit("select", null);
  state.follow = null;
  state.focusLines = null;
  try {
    const network = await api.network(info.id);
    state.provider = info;
    state.network = network;
    state.lines = new Map(network.lines.map((l) => [l.id, { ...l, geom: new LineGeometry(l.shape, l.loop) }]));
    state.stations = new Map(network.stations.map((s) => [s.id, s]));
    state.vehicles = new Map();
    state.userLocation = null;
    history.replaceState(null, "", `#${info.id}`);

    document.querySelectorAll("#city-switch button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === info.id)));
    $("city-name").textContent = info.city;
    $("attribution").textContent = info.attribution;
    emit("providerChanged", info);

    await mapView.loadProvider(info, network);
    realtime.connect(info.id);
    showView("home");
    emit("networkLoaded");
  } catch (e) {
    emit("toast", { text: `Couldn't load ${info.name}: ${e.message}`, error: true, ms: 8000 });
    if (!state.provider && info.id !== "augsburg") return loadProvider("augsburg");
  } finally {
    setTimeout(() => loader.classList.add("done"), 300);
  }
}

function renderCitySwitch() {
  $("city-switch").innerHTML = state.providers.map((p) => `<button data-id="${p.id}" title="${p.description}">${p.city}</button>`).join("");
  $("city-switch").querySelectorAll("button").forEach((b) => (b.onclick = () => b.dataset.id !== state.provider?.id && loadProvider(b.dataset.id)));
}

// ---------------------------------------------------------------- selection
on("select", (sel) => {
  if (sel?.kind !== "vehicle" || state.selection?.id !== sel.id) {
    if (state.follow) { state.follow = null; emit("follow", null); }
  }
  state.selection = sel;
  mapView.select(sel);
});

// ---------------------------------------------------------------- views & sheet
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === name));
  $("side").classList.remove("collapsed");
  if (name === "home" && state.journeys.length) { state.journeys = []; mapView.showJourney(null); }
  if (name === "home" && state.focusLines && !state.selection) { state.focusLines = null; emit("focusLines"); }
}
on("view", showView);
document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => showView("home")));
document.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => showView(b.dataset.go)));
$("side-collapse").onclick = () => $("side").classList.add("collapsed");
$("side-open").onclick = () => $("side").classList.remove("collapsed");
// phones: the sheet peeks at half height; the handle (or focusing search) expands it
$("grab").onclick = () => $("side").classList.toggle("full");
on("sheetFull", (full) => window.innerWidth < 820 && $("side").classList.toggle("full", full));
on("select", (sel) => sel && window.innerWidth < 820 && $("side").classList.remove("full"));

// ---------------------------------------------------------------- mode filter
state.modes = new Set(["tram", "bus"]);
document.querySelectorAll("#mode-filter button").forEach((b) => (b.onclick = () => {
  const m = b.dataset.mode;
  // never hide everything: turning off the last mode switches to the other one
  if (state.modes.has(m) && state.modes.size === 1) state.modes = new Set(["tram", "bus"].filter((x) => x !== m));
  else state.modes.has(m) ? state.modes.delete(m) : state.modes.add(m);
  document.querySelectorAll("#mode-filter button").forEach((x) => x.setAttribute("aria-pressed", String(state.modes.has(x.dataset.mode))));
  mapView.setModes(state.modes);
}));
on("providerChanged", () => {
  const modes = new Set(state.network.lines.map((l) => l.mode));
  $("mode-filter").hidden = !(modes.has("tram") && modes.has("bus"));
  state.modes = modes;
  document.querySelectorAll("#mode-filter button").forEach((x) => x.setAttribute("aria-pressed", "true"));
});

// ---------------------------------------------------------------- map controls
$("controls").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) act(b.dataset.act);
});
function act(name) {
  if (name === "zoom-in") mapView.zoomBy(1);
  if (name === "zoom-out") mapView.zoomBy(-1);
  if (name === "locate") { showView("home"); emit("locate"); }
  if (name === "reset") { state.focusLines = null; emit("focusLines"); mapView.resetView(); }
  if (name === "city") { const on3d = mapView.setCityView(!mapView.cityView); $("controls").querySelector('[data-act="city"]').classList.toggle("on", on3d); }
}

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea")) return;
  const k = e.key.toLowerCase();
  if (k === "escape") { mapView.setPickMode(null); emit("select", null); }
  else if (k === "r") act("reset");
  else if (k === "/") { e.preventDefault(); $("omni-input").focus(); }
  else if (k === "f" && state.selection?.kind === "vehicle") { state.follow = state.follow ? null : "track"; emit("follow", state.follow); }
});

// ---------------------------------------------------------------- clock
const tickClock = () => ($("hud-clock").textContent = new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }));
setInterval(tickClock, 5000);
tickClock();

// ---------------------------------------------------------------- boot
(async function boot() {
  try {
    const [health, providers] = await Promise.all([api.health(), api.providers()]);
    state.providers = providers;
    emit("aiEngine", health.ai);
    renderCitySwitch();
    const wanted = location.hash.slice(1);
    const first = providers.some((p) => p.id === wanted) ? wanted : health.default_provider;
    await loadProvider(first);
  } catch (e) {
    $("loader").querySelector(".loader-text").textContent = `The server isn't responding (${e.message}). Check that it's running, then reload.`;
  }
})();
