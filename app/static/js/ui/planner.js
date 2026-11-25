// Journey planner panel.
import { api } from "../core/api.js";
import { state, emit, on } from "../core/store.js";
import { arrival, distance, escapeHtml, eta, inTime, minutes } from "../core/format.js";
import { haversine } from "../core/geo.js";
import { attachAutocomplete } from "./autocomplete.js";
import { lineBadge } from "./badge.js";

const WALK_ICON = `<svg viewBox="0 0 24 24"><circle cx="13" cy="4" r="2"/><path d="M9 21l2-6 3 2v5M11 15l1-6 4 3h3M12 9l-4 2-1 4"/></svg>`;

const EXAMPLES = {
  augsburg: [["Hauptbahnhof", "Universität"], ["Oberhausen Nord P+R", "Göggingen"], ["Firnhaberau", "Messe"]],
  mbta: [["Harvard", "Park Street"], ["Alewife", "South Station"], ["Ashmont", "Government Center"]],
};

export function initPlanner(mapView) {
  const form = document.getElementById("plan-form");
  const fromIn = document.getElementById("from-input");
  const toIn = document.getElementById("to-input");
  const results = document.getElementById("plan-results");
  const go = document.getElementById("plan-go");
  let plannedAt = 0;

  const setPlace = (which, place, label) => {
    state[which] = place;
    (which === "origin" ? fromIn : toIn).value = label;
  };

  attachAutocomplete(fromIn, (s) => setPlace("origin", { station_id: s.id }, s.name));
  attachAutocomplete(toIn, (s) => setPlace("destination", { station_id: s.id }, s.name));
  // typing without choosing still works: the backend fuzzy-matches the text
  fromIn.addEventListener("input", () => (state.origin = fromIn.value.trim() ? { query: fromIn.value.trim() } : null));
  toIn.addEventListener("input", () => (state.destination = toIn.value.trim() ? { query: toIn.value.trim() } : null));

  document.getElementById("from-pick").onclick = () => mapView.setPickMode("origin");
  document.getElementById("to-pick").onclick = () => mapView.setPickMode("destination");
  on("mapPick", ({ which, lat, lon }) => {
    setPlace(which, { lat, lon, label: "Point on map" }, `Point on map (${lat.toFixed(4)}, ${lon.toFixed(4)})`);
    if (state.origin && state.destination) form.requestSubmit();
  });

  document.getElementById("swap").onclick = () => {
    [state.origin, state.destination] = [state.destination, state.origin];
    [fromIn.value, toIn.value] = [toIn.value, fromIn.value];
  };

  document.getElementById("from-gps").onclick = () => locateUser(mapView, (lat, lon, label) => setPlace("origin", { lat, lon, label }, label));

  on("planFrom", (s) => { setPlace("origin", { station_id: s.id }, s.name); emit("tab", "plan"); toIn.focus(); maybeGo(); });
  on("planTo", (s) => { setPlace("destination", { station_id: s.id }, s.name); emit("tab", "plan"); if (!state.origin) fromIn.focus(); maybeGo(); });
  function maybeGo() { if (state.origin && state.destination) form.requestSubmit(); }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.origin || !state.destination) {
      emit("toast", { text: "Add a start and a destination first." });
      return;
    }
    go.disabled = true;
    go.textContent = "Finding routes";
    try {
      const pref = form.querySelector("input[name=pref]:checked").value;
      const res = await api.plan(state.provider.id, { origin: state.origin, destination: state.destination, preference: pref, alternatives: 4 });
      showJourneys(res.journeys);
    } catch (err) {
      emit("toast", { text: err.message, error: true });
    } finally {
      go.disabled = false;
      go.textContent = "Find routes";
    }
  });

  on("journeys", (js) => { showJourneys(js); });
  on("providerChanged", () => {
    state.origin = state.destination = null;
    fromIn.value = toIn.value = "";
    clearResults();
  });
  clearResults();

  function clearResults() {
    state.journeys = [];
    state.activeJourney = -1;
    mapView.showJourney(null);
    const ex = EXAMPLES[state.provider?.id] || [];
    results.innerHTML = `
      <div class="intro">
        <p>Type two stops, pick points on the map, or start from where you are. Waiting times come from the vehicles you can see moving.</p>
        ${ex.length ? `<div class="try">${ex.map(([a, b]) => `<button type="button" data-a="${escapeHtml(a)}" data-b="${escapeHtml(b)}">${escapeHtml(a)} to ${escapeHtml(b)}</button>`).join("")}</div>` : ""}
      </div>`;
    results.querySelectorAll(".try button").forEach((b) => (b.onclick = () => {
      setPlace("origin", { query: b.dataset.a }, b.dataset.a);
      setPlace("destination", { query: b.dataset.b }, b.dataset.b);
      form.requestSubmit();
    }));
  }

  function showJourneys(journeys) {
    plannedAt = Date.now();
    state.journeys = journeys;
    if (!journeys.length) return clearResults();
    results.innerHTML = journeys.map((j, i) => card(j, i)).join("");
    results.querySelectorAll(".journey").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-track]")) return;
        activate(+el.dataset.i);
      });
    });
    results.querySelectorAll("[data-track]").forEach((b) => b.addEventListener("click", () => {
      emit("select", { kind: "vehicle", id: b.dataset.track });
      state.follow = "track";
      emit("follow", "track");
    }));
    activate(0);
  }

  function activate(i) {
    state.activeJourney = i;
    results.querySelectorAll(".journey").forEach((el) => el.classList.toggle("active", +el.dataset.i === i));
    const j = state.journeys[i];
    mapView.showJourney(j);
    const ids = new Set(j.legs.filter((l) => l.line_id).map((l) => l.line_id));
    state.focusLines = ids.size ? ids : null;
    emit("focusLines");
  }

  function card(j, i) {
    const rides = j.legs.filter((l) => l.kind === "ride");
    const firstRide = rides[0];
    let departIn = 0;
    for (const l of j.legs) { if (l === firstRide) { departIn += l.wait_s; break; } departIn += l.duration_s + (l.wait_s || 0); }
    const strip = j.legs.map((l) => l.kind === "walk"
      ? `<span class="walk">${WALK_ICON}${minutes(l.duration_s)}</span>`
      : lineBadge(l.line_id, l.line_name)).join(`<span class="sep">›</span>`);
    const tag = j.tags[0] === "Fastest" ? "Fastest" : j.tags.includes("Fewest transfers") ? "Fewest changes" : j.tags.includes("Least walking") ? "Least walking" : j.tags[0] === "Walk only" ? "Walk" : "";
    const legs = j.legs.map((l) => l.kind === "walk" ? `
      <div class="leg walk"><span class="leg-rail"></span>
        <div><div class="leg-title">Walk ${distance(l.distance_m)}</div><div class="leg-sub">${escapeHtml(l.from_name)} to ${escapeHtml(l.to_name)}</div></div>
        <div class="leg-time">${minutes(l.duration_s)} min</div></div>` : `
      <div class="leg"><span class="leg-rail" style="background:${l.color}"></span>
        <div>
          <div class="leg-title">${escapeHtml(l.line_name)} towards ${escapeHtml(l.headsign)}</div>
          <div class="leg-sub">${escapeHtml(l.from_name)} to ${escapeHtml(l.to_name)}, ${l.stops.length - 1} stop${l.stops.length === 2 ? "" : "s"}</div>
          ${l.next_vehicle_id ? `<button class="track" data-track="${escapeHtml(l.next_vehicle_id)}">Follow this ${state.lines.get(l.line_id)?.mode === "bus" ? "bus" : "tram"} on the map</button>` : ""}
        </div>
        <div class="leg-time">${l.wait_s > 30 ? `${eta(l.wait_s)} wait<br/>` : ""}${minutes(l.duration_s)} min</div></div>`).join("");
    const firstMode = firstRide && state.lines.get(firstRide.line_id)?.mode === "bus" ? "bus" : "tram";
    return `
      <article class="journey" data-i="${i}">
        <div class="j-top">
          <span class="j-min">${minutes(j.duration_s)}<small>min</small></span>
          <span class="j-when">arrive <span data-arrive="${j.duration_s}">${arrival(j.duration_s)}</span></span>
          ${tag ? `<span class="j-tag">${tag}</span>` : ""}
        </div>
        <div class="j-strip">${strip}</div>
        ${firstRide ? `<div class="j-next">Your ${firstMode} leaves ${escapeHtml(firstRide.from_name)} <b data-wait-in="${departIn}">${inTime(departIn)}</b>${j.transfers ? `, ${j.transfers} change${j.transfers > 1 ? "s" : ""}` : ""}</div>` : `<div class="j-next">${distance(j.walking_m)} on foot</div>`}
        <div class="legs">${legs}</div>
      </article>`;
  }

  // live countdowns
  setInterval(() => {
    if (!state.journeys.length) return;
    const elapsed = (Date.now() - plannedAt) / 1000;
    results.querySelectorAll("[data-wait-in]").forEach((el) => (el.textContent = inTime(Math.max(0, +el.dataset.waitIn - elapsed))));
  }, 1000);
}

export function locateUser(mapView, done) {
  const fallback = (reason) => {
    // Outside the network area (or no permission): start near a random stop instead,
    // so the feature can still be tried.
    const stations = [...state.stations.values()];
    const s = stations[Math.floor(Math.random() * stations.length)];
    const lat = s.lat + (Math.random() - 0.5) * 0.008, lon = s.lon + (Math.random() - 0.5) * 0.01;
    emit("toast", { text: `${reason} Starting you near ${s.name} instead.` });
    finish(lat, lon, "Your location (simulated)");
  };
  const finish = (lat, lon, label) => {
    state.userLocation = { lat, lon };
    mapView.setUserLocation(lat, lon);
    done?.(lat, lon, label);
  };
  if (!navigator.geolocation) return fallback("Location isn't available in this browser.");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude: lat, longitude: lon } = pos.coords;
      const [w, s, e, n] = state.network.bbox;
      const inside = lon > w - 0.05 && lon < e + 0.05 && lat > s - 0.05 && lat < n + 0.05;
      if (!inside) {
        const km = Math.round(haversine([lon, lat], state.provider.center) / 1000);
        return fallback(`You're about ${km} km from ${state.provider.city}.`);
      }
      finish(lat, lon, "Your location");
    },
    () => fallback("Location permission was denied."),
    { enableHighAccuracy: true, timeout: 8000 },
  );
}
