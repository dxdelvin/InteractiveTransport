// App bootstrap: wires the map, realtime feed and UI panels together.
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

const $ = (id) => document.getElementById(id);

initToasts();
const mapView = new MapView("map");
window.__transit = { state, mapView, emit }; // handy for debugging in the console
initPlanner(mapView);
initLines(mapView);
initAssistant();
initInspector(mapView);

// ---------------------------------------------------------------- realtime
const connState = { status: "connecting", count: 0 };
function paintConn(detail) {
  const { status, count } = connState;
  const ok = status === "live" || status === "polling";
  const noun = state.provider?.id === "augsburg" ? "trams and buses" : "vehicles";
  $("hud-conn-label").textContent = ok ? `${count} ${noun} moving` : status === "error" ? "Positions paused" : status === "reconnecting" ? "Reconnecting" : "Connecting";
  $("hud-conn").className = `conn ${ok ? "ok" : status === "error" ? "bad" : ""}`;
  $("hud-conn").title = detail || (state.provider?.kind === "live" ? "Live vehicle positions" : "Positions follow the timetable");
}
const realtime = new Realtime({
  onSnapshot(snap) {
    state.vehicles = new Map(snap.vehicles.map((v) => [v.id, v]));
    mapView.updateVehicles(snap.vehicles);
    connState.count = snap.vehicles.length;
    paintConn();
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
  loader.querySelector(".loader-text").textContent = info.kind === "live" ? `Connecting to the ${info.city} live feed` : "Putting the trams on the tracks";
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
    history.replaceState(null, "", `#${info.id}`);

    document.querySelectorAll("#city-switch button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === info.id)));
    const trams = network.lines.filter((l) => l.mode === "tram").length, buses = network.lines.filter((l) => l.mode === "bus").length;
    $("city-name").textContent = info.city;
    $("city-sub").textContent = trams || buses
      ? `${trams} tram and ${buses} bus lines`
      : `${network.lines.length} lines, live positions`;
    $("attribution").textContent = info.attribution;
    emit("providerChanged", info);

    await mapView.loadProvider(info, network);
    realtime.connect(info.id);
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

// ---------------------------------------------------------------- tabs & panel
function setTab(name) {
  document.querySelectorAll(".mode").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((b) => b.classList.toggle("active", b.dataset.body === name));
  $("side").classList.remove("collapsed");
}
document.querySelectorAll(".mode").forEach((t) => (t.onclick = () => setTab(t.dataset.tab)));
on("tab", setTab);
$("side-collapse").onclick = () => $("side").classList.add("collapsed");
$("side-open").onclick = () => $("side").classList.remove("collapsed");

// ---------------------------------------------------------------- view controls
const controls = $("controls");
const toggles = { buildings: true, labels: true };
controls.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  act(b.dataset.act);
});
function act(name) {
  const btn = controls.querySelector(`[data-act="${name}"]`);
  if (name === "3d") { mapView.set3D(!mapView.is3D); btn.classList.toggle("on", mapView.is3D); }
  if (name === "orbit") mapView.setOrbit(!mapView.orbit);
  if (name === "buildings") { toggles.buildings = !toggles.buildings; mapView.setLayerVisible(["sc-buildings", "osm-buildings"], toggles.buildings); btn.classList.toggle("on", toggles.buildings); }
  if (name === "labels") { toggles.labels = !toggles.labels; mapView.setLayerVisible(["stations-label"], toggles.labels); btn.classList.toggle("on", toggles.labels); }
  if (name === "reset") { state.focusLines = null; emit("focusLines"); mapView.resetView(); }
}
on("orbit", (o) => controls.querySelector('[data-act="orbit"]').classList.toggle("on", o));

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea")) return;
  const k = e.key.toLowerCase();
  if (k === "escape") { mapView.setPickMode(null); emit("select", null); }
  else if (k === "3") act("3d");
  else if (k === "o") act("orbit");
  else if (k === "b") act("buildings");
  else if (k === "l") act("labels");
  else if (k === "r") act("reset");
  else if (k === "/") { e.preventDefault(); setTab("plan"); $("from-input").focus(); }
  else if (k === "f" && state.selection?.kind === "vehicle") { state.follow = state.follow ? null : "track"; emit("follow", state.follow); }
});

// ---------------------------------------------------------------- HUD clock
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
