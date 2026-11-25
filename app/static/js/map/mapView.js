// MapLibre map: basemap, network layers, route overlay, picking, camera modes.
import { state, emit, on } from "../core/store.js";
import { bboxOf } from "../core/geo.js";
import { api } from "../core/api.js";
import { FONT, FONT_BOLD, osmModelStyle, proceduralStyle, sceneryLayers } from "./styles.js";
import { TrainLayer } from "./trainLayer.js";

const EMPTY = { type: "FeatureCollection", features: [] };

export class MapView {
  constructor(container) {
    this.map = new maplibregl.Map({
      container,
      style: proceduralStyle(),
      center: [0, 20],
      zoom: 2,
      pitch: 0,
      maxPitch: 80,
      antialias: true,
      attributionControl: { compact: true },
      fadeDuration: 150,
    });
    this.map.dragRotate.enable();
    this.map.touchZoomRotate.enableRotation();
    this.trains = new TrainLayer({ getLine: (id) => state.lines.get(id) });
    this.pickMode = null;
    this.orbit = false;
    this.is3D = true;
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
        emit("toast", { text: "The street map couldn't load, so you're seeing a schematic city. Stops and lines are unaffected." });
      }
    }
    if (!style) {
      style = proceduralStyle();
      scenery = await api.scenery(info.id).catch(() => null);
      this.basemap = "schematic";
    }
    emit("basemap", this.basemap);
    map.setStyle(style, { diff: false });
    await new Promise((res) => (map.isStyleLoaded() ? res() : map.once("style.load", res)));
    map.setLight({ anchor: "viewport", color: "#ffffff", intensity: 0.32, position: [1.3, 215, 45] });

    if (scenery?.features?.length) {
      map.addSource("scenery", { type: "geojson", data: scenery, tolerance: 0.4 });
      for (const layer of sceneryLayers()) map.addLayer(layer);
    }
    this._addNetworkLayers(network);

    this.trains.clear();
    this.trains.setOrigin(info.center);
    map.addLayer(this.trains, "stations-circle");

    const [w, s, e, n] = network.bbox;
    map.fitBounds([[w, s], [e, n]], { padding: this._padding(), pitch: 0, bearing: 0, duration: 0 });
    // one orchestrated moment: the model tilts up as the city loads
    map.easeTo({ pitch: this._fitPitch(55) || (this.is3D ? 40 : 0), bearing: -12, zoom: map.getZoom() + 0.25, duration: 2400, essential: true });
  }

  /** Tilt for camera fits. Phones fit flat: a tilted camera with a half-screen bottom
   *  padding pushes the scene past MapLibre's far plane and the map renders empty. */
  _fitPitch(pitch) {
    return window.innerWidth < 820 ? 0 : this.is3D ? pitch : 0;
  }

  _padding() {
    const narrow = window.innerWidth < 820;
    const sideOpen = !document.getElementById("side").classList.contains("collapsed");
    const boardOpen = !document.getElementById("inspector").hidden;
    return narrow
      ? { top: 70, bottom: sideOpen ? window.innerHeight * 0.58 : 40, left: 24, right: 24 }
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

    const stationFeatures = network.stations.map((s) => {
      const first = state.lines.get(s.lines[0]);
      return {
        type: "Feature",
        properties: { id: s.id, name: s.name, n: s.lines.length, color: first ? first.color : "#ffffff", lines: s.lines.join(",") },
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      };
    });
    map.addSource("stations", { type: "geojson", data: { type: "FeatureCollection", features: stationFeatures } });
    // Labels get their own source: if the font server is unreachable, MapLibre drops every
    // bucket of a tile that needs glyphs - stops must never disappear along with their names.
    map.addSource("station-labels", { type: "geojson", data: { type: "FeatureCollection", features: stationFeatures } });
    map.addSource("route", { type: "geojson", data: EMPTY, lineMetrics: true });
    map.addSource("route-stops", { type: "geojson", data: EMPTY });

    const width = (base) => ["interpolate", ["exponential", 1.5], ["zoom"], 11, base * 0.45, 13, base, 16, base * 2.2, 18, base * 3.6];
    const isBus = ["==", ["get", "mode"], "bus"];
    // zoom must be the outermost expression, so the bus/tram choice goes inside each stop
    const byMode = (tram, bus) => ["interpolate", ["exponential", 1.5], ["zoom"],
      11, ["case", isBus, bus * 0.45, tram * 0.45], 13, ["case", isBus, bus, tram],
      16, ["case", isBus, bus * 2.2, tram * 2.2], 18, ["case", isBus, bus * 3.6, tram * 3.6]];

    // trams: heavy solid strokes; buses: lighter strokes - both on a white casing so they lift off the model
    map.addLayer({ id: "lines-casing", type: "line", source: "lines", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": byMode(8, 5), "line-opacity": 0.95 } });
    map.addLayer({ id: "lines-core", type: "line", source: "lines", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": byMode(4.2, 2), "line-opacity": 1 } });

    map.addLayer({ id: "route-casing", type: "line", source: "route", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#1e2328", "line-width": width(11) } });
    map.addLayer({ id: "route-core", type: "line", source: "route", filter: ["==", ["get", "kind"], "ride"], layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["get", "color"], "line-width": width(7) } });
    map.addLayer({ id: "route-flow", type: "line", source: "route", filter: ["==", ["get", "kind"], "ride"], paint: { "line-color": "#ffffff", "line-width": width(1.6), "line-opacity": 0.9, "line-dasharray": [0, 4, 3] } });
    map.addLayer({ id: "route-walk", type: "line", source: "route", filter: ["==", ["get", "kind"], "walk"], layout: { "line-cap": "round" }, paint: { "line-color": "#ffffff", "line-width": width(3.6), "line-dasharray": [0.1, 2] } });

    // stops: interchanges wear the yellow-and-green H sign, single-line stops a ring in the line colour
    map.addLayer({
      id: "stations-circle", type: "circle", source: "stations",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, ["case", [">", ["get", "n"], 1], 4, 2.5], 14, ["case", [">", ["get", "n"], 1], 8, 5], 17, ["case", [">", ["get", "n"], 1], 13, 8]],
        "circle-color": ["case", [">", ["get", "n"], 1], "#ffd100", "#ffffff"],
        "circle-stroke-color": ["case", [">", ["get", "n"], 1], "#00704a", ["get", "color"]],
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 11, 1.5, 15, 3],
        "circle-pitch-alignment": "map",
      },
    });
    map.addLayer({ id: "route-stops", type: "circle", source: "route-stops", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 3.5, 15, 7.5], "circle-color": "#ffffff", "circle-stroke-color": "#1e2328", "circle-stroke-width": 3 } });
    map.addLayer({
      id: "stations-label", type: "symbol", source: "station-labels",
      layout: {
        "text-field": ["get", "name"],
        "text-font": ["case", [">", ["get", "n"], 1], ["literal", FONT_BOLD], ["literal", FONT]],
        "text-size": ["interpolate", ["linear"], ["zoom"], 12, ["case", [">", ["get", "n"], 1], 12.5, 11], 16, ["case", [">", ["get", "n"], 1], 16, 14]],
        "text-offset": [0, 1.1],
        "text-anchor": "top",
        "text-max-width": 9,
        "symbol-sort-key": ["-", 0, ["get", "n"]],
        "text-optional": true,
      },
      paint: {
        "text-color": "#1e2328", "text-halo-color": "#ffffff", "text-halo-width": 1.8,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 11.5, ["case", [">", ["get", "n"], 1], 1, 0], 13, ["case", [">", ["get", "n"], 1], 1, 0], 13.4, 1],
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
    const stopFollow = (e) => {
      if (e.originalEvent && (state.follow || this.orbit)) {
        if (state.follow) { state.follow = null; emit("follow", null); }
        if (this.orbit) this.setOrbit(false);
      }
    };
    map.on("dragstart", stopFollow);
    map.on("rotatestart", stopFollow);
    on("focusLines", () => this._applyFocus());
  }

  _onClick(e) {
    if (this.pickMode) {
      const which = this.pickMode;
      this.setPickMode(null);
      emit("mapPick", { which, lat: e.lngLat.lat, lon: e.lngLat.lng });
      return;
    }
    const vid = this.trains.pick(e.point);
    if (vid) return emit("select", { kind: "vehicle", id: vid });
    const feats = this._stationAt(e.point);
    if (feats) return emit("select", { kind: "station", id: feats.properties.id });
    emit("select", null);
  }

  _stationAt(point) {
    if (!this.map.getLayer("stations-circle")) return null;
    const pad = 8;
    const f = this.map.queryRenderedFeatures([[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]], { layers: ["stations-circle"] });
    return f[0] || null;
  }

  _onHover(e) {
    if (!this.map.getLayer("stations-circle")) return;
    const vid = this.trains.pick(e.point);
    if (vid) {
      const v = state.vehicles.get(vid);
      const line = v && state.lines.get(v.line_id);
      if (v && line) {
        const late = v.delay_s >= 60 ? `, ${Math.round(v.delay_s / 60)} min late` : "";
        this._tooltip(e.originalEvent, `<span class="lb${line.mode === "bus" ? " bus" : ""}" style="background:${line.color};color:${line.text_color}">${escape(line.short_name)}</span><span>to ${escape(v.headsign)}<small>${late}</small></span>`);
        this.map.getCanvas().style.cursor = "pointer";
        return;
      }
    }
    const st = this._stationAt(e.point);
    if (st) {
      const badges = st.properties.lines.split(",").map((id) => {
        const l = state.lines.get(id);
        return l ? `<span class="lb${l.mode === "bus" ? " bus" : ""}" style="background:${l.color};color:${l.text_color}">${escape(l.short_name)}</span>` : "";
      }).join("");
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

  setPickMode(which) {
    this.pickMode = which;
    document.getElementById("pick-hint").hidden = !which;
    if (which) document.getElementById("pick-which").textContent = which === "origin" ? "start" : "destination";
    this.map.getCanvas().style.cursor = which ? "crosshair" : "";
  }

  // ------------------------------------------------------------------ frame loop (camera + animation)
  _loop(t) {
    requestAnimationFrame(this._loop);
    const map = this.map;
    if (!map.getLayer("route-flow")) return;

    // marching dashes along the planned route
    if (state.journeys.length && t - this._dash > 60) {
      this._dash = t;
      const step = Math.floor(t / 60) % 14;
      const seq = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
      map.setPaintProperty("route-flow", "line-dasharray", seq[step]);
    }

    const sel = state.selection;
    if (state.follow && sel?.kind === "vehicle") {
      const p = this.trains.display.get(sel.id);
      if (p) {
        if (state.follow === "cab") {
          const cur = map.getBearing();
          let diff = ((p.bearing - cur + 540) % 360) - 180;
          map.jumpTo({ center: [p.lon, p.lat], bearing: cur + diff * 0.06, pitch: 74, zoom: Math.max(map.getZoom(), 15.4) });
        } else {
          map.jumpTo({ center: [p.mid[0], p.mid[1]] });
        }
      }
    } else if (this.orbit) {
      map.setBearing(map.getBearing() + 0.06);
    }
  }

  // ------------------------------------------------------------------ public API
  updateVehicles(vehicles) {
    this.trains.update(vehicles);
  }

  select(sel) {
    this.trains.selectedId = sel?.kind === "vehicle" ? sel.id : null;
    if (sel?.kind === "station") {
      const s = state.stations.get(sel.id);
      if (s) this.map.easeTo({ center: [s.lon, s.lat], zoom: Math.max(this.map.getZoom(), 14), duration: 900 });
    }
  }

  flyToVehicle(id) {
    const p = this.trains.display.get(id);
    if (p) this.map.easeTo({ center: [p.mid[0], p.mid[1]], zoom: Math.max(this.map.getZoom(), 14.6), duration: 1000 });
  }

  _applyFocus() {
    const map = this.map;
    if (!map.getLayer("lines-core")) return;
    const focus = state.focusLines;
    this.trains.focus = focus;
    const expr = (on, off) => (focus ? ["case", ["in", ["get", "id"], ["literal", [...focus]]], on, off] : on);
    map.setPaintProperty("lines-core", "line-opacity", expr(1, 0.18));
    map.setPaintProperty("lines-casing", "line-opacity", expr(0.95, 0.3));
  }

  fitLines(ids) {
    const coords = [];
    for (const id of ids) coords.push(...(state.lines.get(id)?.shape || []));
    if (coords.length) this.map.fitBounds(bboxOf(coords), { padding: this._padding(), duration: 1100, pitch: this._fitPitch(50) });
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
    map.fitBounds(bboxOf(all), { padding: this._padding(), duration: 1200, pitch: this._fitPitch(45), maxZoom: 15.5 });
  }

  setUserLocation(lat, lon) {
    if (!this.userMarker) {
      const el = document.createElement("div");
      el.className = "you";
      this.userMarker = new maplibregl.Marker({ element: el });
    }
    this.userMarker.setLngLat([lon, lat]).addTo(this.map);
  }

  set3D(on) {
    this.is3D = on;
    this.map.easeTo({ pitch: on ? 55 : 0, bearing: on ? this.map.getBearing() : 0, duration: 900 });
  }

  setOrbit(on) {
    this.orbit = on;
    emit("orbit", on);
    if (on && this.map.getPitch() < 40) this.map.easeTo({ pitch: 60, duration: 800 });
  }

  setLayerVisible(ids, on) {
    for (const id of ids) if (this.map.getLayer(id)) this.map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }

  resetView() {
    if (!state.network) return;
    const [w, s, e, n] = state.network.bbox;
    this.map.fitBounds([[w, s], [e, n]], { padding: this._padding(), pitch: this._fitPitch(55), bearing: -12, duration: 1200 });
  }
}

function escape(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
