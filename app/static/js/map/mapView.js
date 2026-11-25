// MapLibre map: street basemap, network layers, vehicles, route overlay, picking, camera.
import { state, emit, on } from "../core/store.js";
import { bboxOf } from "../core/geo.js";
import { api } from "../core/api.js";
import { FONT, FONT_BOLD, osmModelStyle, proceduralStyle, sceneryLayers } from "./styles.js";
import { VehicleLayer } from "./vehicles.js";

const EMPTY = { type: "FeatureCollection", features: [] };

export class MapView {
  constructor(container) {
    this.map = new maplibregl.Map({
      container,
      style: proceduralStyle(),
      center: [0, 20],
      zoom: 2,
      pitch: 0,
      maxPitch: 0,
      dragRotate: false,
      attributionControl: { compact: true },
      fadeDuration: 150,
    });
    this.map.touchZoomRotate.disableRotation();
    this.vehicles = new VehicleLayer(this.map, (id) => state.lines.get(id));
    this.pickMode = null;
    this.cityView = false;
    this.markers = [];
    this.userMarker = null;
    this._dash = 0;
    this._hoverRaf = 0;
    this.ready = new Promise((res) => this.map.once("load", res));
    this._bindEvents();
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  // ------------------------------------------------------------------ provider load
  async loadProvider(info, network) {
    await this.ready;
    const map = this.map;
    let style = null, scenery = null;
    if (info.basemap === "osm") {
      try {
        style = await osmModelStyle();
        this.basemap = "osm";
      } catch {
        emit("toast", { text: "The street map didn't load, so streets are drawn from a simplified plan. Stops, lines and times are unaffected." });
      }
    }
    if (!style) {
      style = proceduralStyle();
      scenery = await api.scenery(info.id).catch(() => null);
      this.basemap = "schematic";
    }
    map.setStyle(style, { diff: false });
    await new Promise((res) => (map.isStyleLoaded() ? res() : map.once("style.load", res)));

    if (scenery?.features?.length) {
      map.addSource("scenery", { type: "geojson", data: scenery, tolerance: 0.4 });
      for (const layer of sceneryLayers()) map.addLayer(layer);
    }
    this._addNetworkLayers(network);
    this.vehicles.install("stations-label");
    this.vehicles.clear();
    if (state.modes) this.setModes(state.modes);
    if (this.cityView) { this.cityView = false; this.setCityView(true); }
  }

  /** Showcase mode: tilted 3D city with OSM buildings and a slow turn. Never the default. */
  setCityView(on) {
    const map = this.map;
    this.cityView = on;
    cancelAnimationFrame(this._spin);
    if (on) {
      map.setMaxPitch(70);
      map.setLight({ anchor: "viewport", color: "#ffffff", intensity: 0.25, position: [1.4, 200, 40] });
      if (!map.getLayer("city-buildings")) {
        const before = map.getLayer("lines-casing") ? "lines-casing" : undefined;
        if (map.getSource("openmaptiles")) {
          map.addLayer({
            id: "city-buildings", type: "fill-extrusion", source: "openmaptiles", "source-layer": "building", minzoom: 13,
            paint: {
              "fill-extrusion-color": ["interpolate", ["linear"], ["coalesce", ["get", "render_height"], 10], 6, "#e6e9e8", 60, "#ffffff"],
              "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 13, 0, 14.5, ["coalesce", ["get", "render_height"], 10]],
              "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
              "fill-extrusion-opacity": 0.92,
              "fill-extrusion-vertical-gradient": true,
            },
          }, before);
        } else if (map.getSource("scenery")) {
          map.addLayer({
            id: "city-buildings", type: "fill-extrusion", source: "scenery", filter: ["==", ["get", "kind"], "building"],
            paint: { "fill-extrusion-color": "#eceeed", "fill-extrusion-height": ["get", "height"], "fill-extrusion-opacity": 0.92 },
          }, before);
        }
      }
      // Rathaus and Perlach tower for Augsburg; otherwise the city centre
      const c = state.provider?.id === "augsburg" ? [10.8986, 48.3687] : state.provider?.center;
      map.easeTo({ center: c, zoom: 15.6, pitch: 62, bearing: -28, duration: 2200, essential: true });
      const spin = () => {
        if (!this.cityView) return;
        if (!map.isMoving()) map.setBearing(map.getBearing() + 0.04);
        this._spin = requestAnimationFrame(spin);
      };
      map.once("moveend", () => { this._spin = requestAnimationFrame(spin); });
    } else {
      if (map.getLayer("city-buildings")) map.removeLayer("city-buildings");
      map.easeTo({ pitch: 0, bearing: 0, duration: 900 });
      map.once("moveend", () => !this.cityView && map.setMaxPitch(0));
    }
    return on;
  }

  _padding() {
    const narrow = window.innerWidth < 820;
    const side = document.getElementById("side");
    const sideOpen = !side.classList.contains("collapsed");
    const boardOpen = !document.getElementById("inspector").hidden;
    return narrow
      ? { top: 60, bottom: sideOpen ? side.getBoundingClientRect().height + 20 : 40, left: 24, right: 60 }
      : { top: 50, bottom: 50, left: sideOpen ? 440 : 60, right: boardOpen ? 430 : 80 };
  }

  _addNetworkLayers(network) {
    const map = this.map;
    const lineFeatures = network.lines.map((l) => ({
      type: "Feature",
      properties: { id: l.id, color: l.color, mode: l.mode, name: l.name },
      geometry: { type: "LineString", coordinates: l.shape },
    }));
    map.addSource("lines", { type: "geojson", data: { type: "FeatureCollection", features: lineFeatures }, lineMetrics: true });

    const stationFeatures = network.stations.map((s) => ({
      type: "Feature",
      properties: { id: s.id, name: s.name, n: s.lines.length, lines: s.lines.join(","), modes: [...new Set(s.lines.map((id) => state.lines.get(id)?.mode))].join(","),
        major: s.lines.some((id) => state.lines.get(id)?.mode !== "bus") || s.lines.length >= 4 },
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    }));
    map.addSource("stations", { type: "geojson", data: { type: "FeatureCollection", features: stationFeatures } });
    // Labels get their own source: if the font server is unreachable, MapLibre drops every
    // bucket of a tile that needs glyphs - stops must never disappear along with their names.
    map.addSource("station-labels", { type: "geojson", data: { type: "FeatureCollection", features: stationFeatures } });
    map.addSource("alert-stops", { type: "geojson", data: EMPTY });
    map.addSource("route", { type: "geojson", data: EMPTY, lineMetrics: true });
    map.addSource("route-stops", { type: "geojson", data: EMPTY });

    const width = (base) => ["interpolate", ["exponential", 1.5], ["zoom"], 11, base * 0.45, 13, base, 16, base * 2.2, 18, base * 3.6];
    const isBus = ["==", ["get", "mode"], "bus"];
    const byMode = (tram, bus) => ["interpolate", ["exponential", 1.5], ["zoom"],
      11, ["case", isBus, bus * 0.5, tram * 0.5], 13, ["case", isBus, bus, tram],
      16, ["case", isBus, bus * 1.8, tram * 1.8], 18, ["case", isBus, bus * 2.6, tram * 2.6]];

    // trams: solid strokes; buses: thinner - both on a white casing so they lift off the streets
    map.addLayer({ id: "lines-casing", type: "line", source: "lines", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": byMode(6.5, 4.5), "line-opacity": 0.9 } });
    map.addLayer({ id: "lines-core", type: "line", source: "lines", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": byMode(3.4, 2), "line-opacity": 0.9 } });

    map.addLayer({ id: "route-casing", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#1b2420", "line-width": width(11) } });
    map.addLayer({ id: "route-core", type: "line", source: "route", filter: ["==", ["get", "kind"], "ride"], layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["get", "color"], "line-width": width(7) } });
    map.addLayer({ id: "route-flow", type: "line", source: "route", filter: ["==", ["get", "kind"], "ride"], paint: { "line-color": "#ffffff", "line-width": width(1.6), "line-opacity": 0.9, "line-dasharray": [0, 4, 3] } });
    map.addLayer({ id: "route-walk", type: "line", source: "route", filter: ["==", ["get", "kind"], "walk"], layout: { "line-cap": "round" }, paint: { "line-color": "#1b2420", "line-width": width(3.2), "line-dasharray": [0.1, 2] } });

    // stops with a reported delay get a red or amber halo
    map.addLayer({
      id: "alert-stops", type: "circle", source: "alert-stops",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 8, 15, 16],
        "circle-color": ["match", ["get", "severity"], "major", "#d2381e", "#e89b0c"],
        "circle-opacity": 0.22,
        "circle-stroke-color": ["match", ["get", "severity"], "major", "#d2381e", "#e89b0c"],
        "circle-stroke-width": 2,
      },
    });
    // stops: tram stops and big interchanges wear the yellow-and-green H sign; bus stops are small rings
    const major = ["get", "major"];
    map.addLayer({
      id: "stations-circle", type: "circle", source: "stations",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, ["case", major, 3, 1.2], 14, ["case", major, 6, 3], 17, ["case", major, 10, 6]],
        "circle-color": ["case", major, "#ffd100", "#ffffff"],
        "circle-stroke-color": ["case", major, "#00704a", "#4c5752"],
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 11, ["case", major, 1.2, 0.6], 15, ["case", major, 2.4, 1.6]],
      },
    });
    map.addLayer({ id: "route-stops", type: "circle", source: "route-stops", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 3.5, 15, 7.5], "circle-color": "#ffffff", "circle-stroke-color": "#1b2420", "circle-stroke-width": 3 } });
    map.addLayer({
      id: "stations-label", type: "symbol", source: "station-labels",
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["case", ["get", "major"], ["literal", FONT_BOLD], ["literal", FONT]],
        "text-size": ["interpolate", ["linear"], ["zoom"], 12, ["case", ["get", "major"], 12, 10.5], 16, ["case", ["get", "major"], 15, 13]],
        "text-offset": [0, 1],
        "text-anchor": "top",
        "text-max-width": 9,
        "symbol-sort-key": ["-", 0, ["get", "n"]],
        "text-optional": true,
      },
      paint: {
        "text-color": "#1b2420", "text-halo-color": "#ffffff", "text-halo-width": 1.8,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 11.5, ["case", ["get", "major"], 1, 0], 14.5, ["case", ["get", "major"], 1, 0], 15, 1],
      },
    });
  }

  // ------------------------------------------------------------------ events
  _bindEvents() {
    const map = this.map;
    map.on("click", (e) => this._onClick(e));
    map.on("mousemove", (e) => {
      cancelAnimationFrame(this._hoverRaf);
      this._hoverRaf = requestAnimationFrame(() => this._onHover(e));
    });
    map.on("mouseout", () => this._tooltip(null));
    map.on("dragstart", (e) => {
      if (e.originalEvent && state.follow) { state.follow = null; emit("follow", null); }
    });
    // in city view, dragging with the right button / two fingers tilts and turns
    map.on("mousedown", () => cancelAnimationFrame(this._spin));
    on("focusLines", () => this._applyFocus());
    on("alerts", (alerts) => this._showAlertStops(alerts));
  }

  _onClick(e) {
    if (this.pickMode) {
      const which = this.pickMode;
      this.setPickMode(null);
      emit("mapPick", { which, lat: e.lngLat.lat, lon: e.lngLat.lng });
      return;
    }
    const vid = this.vehicles.pick(e.point);
    if (vid) return emit("select", { kind: "vehicle", id: vid });
    const st = this._stationAt(e.point);
    if (st) return emit("select", { kind: "station", id: st.properties.id });
    emit("select", null);
  }

  _stationAt(point) {
    if (!this.map.getLayer("stations-circle")) return null;
    const pad = 10;
    const f = this.map.queryRenderedFeatures([[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]], { layers: ["stations-circle"] });
    return f[0] || null;
  }

  _onHover(e) {
    if (!this.map.getLayer("stations-circle")) return;
    const vid = this.vehicles.pick(e.point);
    if (vid) {
      const v = state.vehicles.get(vid);
      const line = v && state.lines.get(v.line_id);
      if (v && line) {
        const late = v.delay_s >= 60 ? `<small class="late">+${Math.round(v.delay_s / 60)} min</small>` : "";
        this._tooltip(e.originalEvent, `${badge(line)}<span>to ${escape(v.headsign)}</span>${late}`);
        this.map.getCanvas().style.cursor = "pointer";
        return;
      }
    }
    const st = this._stationAt(e.point);
    if (st) {
      const badges = st.properties.lines.split(",").map((id) => (state.lines.get(id) ? badge(state.lines.get(id)) : "")).join("");
      this._tooltip(e.originalEvent, `<b>${escape(st.properties.name)}</b>${badges}`);
      this.map.getCanvas().style.cursor = "pointer";
      return;
    }
    this._tooltip(null);
    this.map.getCanvas().style.cursor = this.pickMode ? "crosshair" : "";
  }

  _tooltip(ev, html) {
    const el = document.getElementById("tooltip");
    if (!ev) { el.hidden = true; return; }
    el.innerHTML = html;
    el.style.left = ev.clientX + "px";
    el.style.top = ev.clientY + "px";
    el.hidden = false;
  }

  _showAlertStops(alerts) {
    const feats = (alerts || []).filter((a) => a.station_id && a.severity !== "no_service").map((a) => {
      const s = state.stations.get(a.station_id);
      return s && { type: "Feature", properties: { severity: a.severity }, geometry: { type: "Point", coordinates: [s.lon, s.lat] } };
    }).filter(Boolean);
    this.map.getSource("alert-stops")?.setData({ type: "FeatureCollection", features: feats });
  }

  setPickMode(which) {
    this.pickMode = which;
    document.getElementById("pick-hint").hidden = !which;
    if (which) document.getElementById("pick-which").textContent = which === "origin" ? "start" : "destination";
    this.map.getCanvas().style.cursor = which ? "crosshair" : "";
  }

  // ------------------------------------------------------------------ frame loop
  _loop(t) {
    requestAnimationFrame(this._loop);
    const map = this.map;
    if (!map.getLayer("route-flow")) return;
    this.vehicles.frame(t);

    // marching dashes along the planned route
    if (state.journeys.length && t - this._dash > 60) {
      this._dash = t;
      const step = Math.floor(t / 60) % 14;
      const seq = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
      map.setPaintProperty("route-flow", "line-dasharray", seq[step]);
    }

    const sel = state.selection;
    if (state.follow && sel?.kind === "vehicle") {
      const p = this.vehicles.display.get(sel.id);
      if (p) map.jumpTo({ center: [p.lon, p.lat] });
    }
  }

  // ------------------------------------------------------------------ public API
  updateVehicles(vehicles) {
    this.vehicles.update(vehicles);
  }

  select(sel) {
    this.vehicles.select(sel?.kind === "vehicle" ? sel.id : null);
    if (sel?.kind === "station") {
      const s = state.stations.get(sel.id);
      if (s) this.map.easeTo({ center: [s.lon, s.lat], zoom: Math.max(this.map.getZoom(), 15), duration: 800, padding: this._padding() });
    }
    // a selected vehicle brings its line forward; clearing it restores what was focused before
    if (sel?.kind === "vehicle") {
      const v = state.vehicles.get(sel.id);
      if (v) {
        if (this._prevFocus === undefined) this._prevFocus = state.focusLines;
        state.focusLines = new Set([v.line_id]);
        emit("focusLines");
      }
    } else if (this._prevFocus !== undefined) {
      state.focusLines = this._prevFocus;
      this._prevFocus = undefined;
      emit("focusLines");
    }
  }

  flyToVehicle(id) {
    const p = this.vehicles.display.get(id);
    if (p) this.map.easeTo({ center: [p.lon, p.lat], zoom: Math.max(this.map.getZoom(), 15), duration: 900, padding: this._padding() });
  }

  flyTo(lon, lat, zoom = 15.2, instant = false) {
    this.map.easeTo({ center: [lon, lat], zoom, duration: instant ? 0 : 1000, padding: this._padding() });
  }

  _applyFocus() {
    const map = this.map;
    if (!map.getLayer("lines-core")) return;
    const focus = state.focusLines;
    this.vehicles.focus = focus;
    const expr = (on, off) => (focus ? ["case", ["in", ["get", "id"], ["literal", [...focus]]], on, off] : on);
    map.setPaintProperty("lines-core", "line-opacity", expr(1, 0.2));
    map.setPaintProperty("lines-casing", "line-opacity", expr(0.95, 0.3));
  }

  fitLines(ids) {
    const coords = [];
    for (const id of ids) coords.push(...(state.lines.get(id)?.shape || []));
    if (coords.length) this.map.fitBounds(bboxOf(coords), { padding: this._padding(), duration: 1000 });
  }

  showJourney(j) {
    const map = this.map;
    this.markers.forEach((m) => m.remove());
    this.markers = [];
    if (!j) {
      map.getSource("route")?.setData(EMPTY);
      map.getSource("route-stops")?.setData(EMPTY);
      return;
    }
    const features = j.legs.map((l) => ({ type: "Feature", properties: { kind: l.kind, color: l.color || "#fff" }, geometry: { type: "LineString", coordinates: l.geometry } }));
    map.getSource("route").setData({ type: "FeatureCollection", features });
    const stops = [];
    for (const l of j.legs) {
      if (l.kind !== "ride") continue;
      for (const sid of [l.from_station_id, l.to_station_id]) {
        const s = state.stations.get(sid);
        if (s) stops.push({ type: "Feature", properties: { color: l.color }, geometry: { type: "Point", coordinates: [s.lon, s.lat] } });
      }
    }
    map.getSource("route-stops").setData({ type: "FeatureCollection", features: stops });
    const all = j.legs.flatMap((l) => l.geometry);
    const start = all[0], end = all[all.length - 1];
    for (const [pt, cls] of [[start, "a"], [end, "b"]]) {
      const el = document.createElement("div");
      el.className = `endpoint ${cls}`;
      this.markers.push(new maplibregl.Marker({ element: el }).setLngLat(pt).addTo(map));
    }
    map.fitBounds(bboxOf(all), { padding: this._padding(), duration: 1100, maxZoom: 15.5 });
  }

  setUserLocation(lat, lon) {
    if (!this.userMarker) {
      const el = document.createElement("div");
      el.className = "you";
      this.userMarker = new maplibregl.Marker({ element: el });
    }
    this.userMarker.setLngLat([lon, lat]).addTo(this.map);
  }

  /** Show only some modes (trams, buses) on the map. */
  setModes(modes) {
    const map = this.map;
    this.vehicles.modes = modes;
    const lineF = ["in", ["get", "mode"], ["literal", [...modes]]];
    const stF = ["any", ...[...modes].map((m) => ["in", m, ["get", "modes"]])];
    for (const id of ["lines-casing", "lines-core"]) if (map.getLayer(id)) map.setFilter(id, lineF);
    for (const id of ["stations-circle", "stations-label"]) if (map.getLayer(id)) map.setFilter(id, modes.size ? stF : ["==", 1, 0]);
  }

  zoomBy(d) { this.map.easeTo({ zoom: this.map.getZoom() + d, duration: 300 }); }

  resetView() {
    if (!state.network) return;
    const [w, s, e, n] = state.network.bbox;
    this.map.fitBounds([[w, s], [e, n]], { padding: this._padding(), duration: 1000 });
  }
}

function badge(l) {
  return `<span class="lb${l.mode === "bus" ? " bus" : ""}" style="background:${l.color};color:${l.text_color}">${escape(l.short_name)}</span>`;
}

function escape(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
