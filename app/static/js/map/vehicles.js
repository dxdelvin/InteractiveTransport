// Vehicles as small map symbols: a dot in the line colour with the line number, a pointer
// showing the direction of travel, and a red/amber tag when the vehicle is late.
//
// * Positions glide between server snapshots along the line shape (by distance along the
//   line, wrap-aware on loops), so motion stays smooth at any update rate.
// * Symbols scale with zoom: dots at city scale, numbered dots once you zoom in.
// * Timetable-estimated vehicles get a white ring; GPS-tracked ones a solid one. The panel
//   says which is which.
const SRC = "vehicles";
const PATH_SRC = "vehicle-path";
const EMPTY = { type: "FeatureCollection", features: [] };
const FRAME_MS = 50;

export const VEHICLE_LAYERS = ["veh-arrow", "veh-dot", "veh-label"];

export class VehicleLayer {
  constructor(map, getLine) {
    this.map = map;
    this.getLine = getLine;
    this.tracks = new Map();     // id -> glide state + latest vehicle
    this.display = new Map();    // id -> { lon, lat, bearing, lineId, dist, dir }
    this.selectedId = null;
    this.focus = null;
    this.interval = 1000;
    this.lastSnapshotAt = 0;
    this._last = 0;
    this._pathAt = 0;
    this._images = new Set();
  }

  /** Add sources and layers. Call after every style load. */
  install(beforeId) {
    const map = this.map;
    this._images.clear();
    map.addSource(SRC, { type: "geojson", data: EMPTY });
    map.addSource(PATH_SRC, { type: "geojson", data: EMPTY });
    const r = (k) => ["interpolate", ["linear"], ["zoom"], 10, 3 * k, 12, 4.5 * k, 14, 8 * k, 16, 11 * k, 18, 13 * k];
    const dim = ["case", ["get", "dim"], 0.25, 1];

    map.addLayer({ id: "veh-path-casing", type: "line", source: PATH_SRC, layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 7, 16, 14] } }, beforeId);
    map.addLayer({ id: "veh-path", type: "line", source: PATH_SRC, layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ["interpolate", ["linear"], ["zoom"], 11, 4, 16, 8] } }, beforeId);

    map.addLayer({
      id: "veh-arrow", type: "symbol", source: SRC,
      layout: {
        "icon-image": ["concat", "veh-arrow-", ["get", "color"]],
        "icon-size": ["interpolate", ["linear"], ["zoom"], 10, 0.38, 12, 0.56, 14, 1, 16, 1.38, 18, 1.62],
        "icon-rotate": ["get", "bearing"],
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true, "icon-ignore-placement": true,
        "symbol-sort-key": ["get", "z"],
      },
      paint: { "icon-opacity": dim },
    }, beforeId);
    map.addLayer({
      id: "veh-sel", type: "circle", source: SRC, filter: ["get", "sel"],
      paint: { "circle-radius": r(1.9), "circle-color": ["get", "color"], "circle-opacity": 0.22, "circle-stroke-color": ["get", "color"], "circle-stroke-width": 2 },
    }, beforeId);
    map.addLayer({
      id: "veh-dot", type: "circle", source: SRC,
      layout: { "circle-sort-key": ["get", "z"] },
      paint: {
        "circle-radius": r(1),
        "circle-color": ["get", "color"],
        "circle-stroke-color": ["match", ["get", "sev"], "major", "#d2381e", "minor", "#e89b0c", "#ffffff"],
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 11, ["case", [">", ["get", "late"], 0], 1.5, 1], 15, ["case", [">", ["get", "late"], 0], 2.6, 1.8]],
        "circle-opacity": dim, "circle-stroke-opacity": dim,
        "circle-pitch-alignment": "map",
      },
    }, beforeId);
    map.addLayer({
      id: "veh-label", type: "symbol", source: SRC, minzoom: 13.2,
      layout: {
        "text-field": ["get", "label"],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 13.2, 9, 16, 13, 18, 15],
        "text-allow-overlap": true, "text-ignore-placement": true,
        "symbol-sort-key": ["get", "z"],
      },
      paint: { "text-color": ["get", "text"], "text-opacity": dim },
    }, beforeId);
    map.addLayer({
      id: "veh-late", type: "symbol", source: SRC, minzoom: 12.5, filter: [">", ["get", "late"], 0],
      layout: {
        "text-field": ["concat", "+", ["to-string", ["get", "late"]]],
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 12.5, 10, 16, 13],
        "text-anchor": "left",
        "text-offset": [0.9, -0.9],
        "text-allow-overlap": true, "text-ignore-placement": true,
      },
      paint: {
        "text-color": ["match", ["get", "sev"], "major", "#b42d16", "#94600a"],
        "text-halo-color": "#ffffff", "text-halo-width": 1.6,
        "text-opacity": dim,
      },
    }, beforeId);
  }

  /** A pointer shape in a line colour, drawn once per colour. */
  _ensureArrow(color) {
    const name = `veh-arrow-${color}`;
    if (this._images.has(name) || this.map.hasImage(name)) { this._images.add(name); return; }
    const S = 64, c = document.createElement("canvas");
    c.width = c.height = S;
    const g = c.getContext("2d");
    // a teardrop: the tip points to the top (bearing 0), the round part sits under the dot
    g.beginPath();
    g.moveTo(S / 2, 3);
    g.lineTo(S / 2 + 13, 24);
    g.arc(S / 2, S / 2, 16.5, -Math.PI * 0.18, Math.PI * 1.18, false);
    g.closePath();
    g.fillStyle = color;
    g.fill();
    g.lineWidth = 3;
    g.strokeStyle = "#ffffff";
    g.stroke();
    this.map.addImage(name, g.getImageData(0, 0, S, S), { pixelRatio: 2 });
    this._images.add(name);
  }

  clear() {
    this.tracks.clear();
    this.display.clear();
    this.lastSnapshotAt = 0;
    this.map.getSource(SRC)?.setData(EMPTY);
    this.map.getSource(PATH_SRC)?.setData(EMPTY);
  }

  update(vehicles) {
    const now = performance.now();
    if (this.lastSnapshotAt) {
      const gap = now - this.lastSnapshotAt;
      this.interval = Math.min(15000, Math.max(800, this.interval * 0.6 + gap * 0.4));
    }
    this.lastSnapshotAt = now;
    const seen = new Set();
    for (const v of vehicles) {
      const line = this.getLine(v.line_id);
      if (!line) continue;
      seen.add(v.id);
      const tr = this.tracks.get(v.id);
      if (!tr || tr.lineId !== v.line_id || tr.dir !== v.direction) {
        this.tracks.set(v.id, { lineId: v.line_id, dir: v.direction, from: v.shape_dist_m, to: v.shape_dist_m, t0: now, dur: 1, v });
        continue;
      }
      const current = this._distAt(tr, now, line.geom);
      const jump = Math.abs(line.geom.delta(current, v.shape_dist_m));
      if (jump > 2500) Object.assign(tr, { from: v.shape_dist_m, to: v.shape_dist_m, t0: now, dur: 1 });
      else Object.assign(tr, { from: current, to: current + line.geom.delta(current, v.shape_dist_m), t0: now, dur: this.interval * 1.05 });
      tr.v = v;
    }
    for (const id of this.tracks.keys()) if (!seen.has(id)) { this.tracks.delete(id); this.display.delete(id); }
  }

  _distAt(tr, now, geom) {
    const f = Math.min(1, Math.max(0, (now - tr.t0) / tr.dur));
    return geom.wrap(tr.from + (tr.to - tr.from) * f);
  }

  /** Called every animation frame; pushes new positions at most every FRAME_MS. */
  frame(now) {
    if (now - this._last < FRAME_MS) return;
    this._last = now;
    const src = this.map.getSource(SRC);
    if (!src) return;
    const features = [];
    for (const [id, tr] of this.tracks) {
      const line = this.getLine(tr.lineId);
      if (!line) continue;
      if (this.modes && !this.modes.has(line.mode) && id !== this.selectedId) continue;
      const dist = this._distAt(tr, now, line.geom);
      const [lon, lat, brg] = line.geom.at(dist);
      const bearing = tr.dir === 0 ? brg : (brg + 180) % 360;
      this.display.set(id, { lon, lat, bearing, lineId: tr.lineId, dist, dir: tr.dir });
      const delay = tr.v.delay_s || 0;
      const late = delay >= 120 ? Math.round(delay / 60) : 0;
      const sel = id === this.selectedId;
      this._ensureArrow(line.color);
      features.push({
        type: "Feature",
        properties: {
          id, color: line.color, text: line.text_color || "#ffffff", label: line.short_name, bearing,
          late, sev: late >= 5 ? "major" : late ? "minor" : "ok", sel,
          dim: !!(this.focus && !this.focus.has(tr.lineId)) && !sel,
          z: sel ? 3 : late ? 2 : 1,
        },
        geometry: { type: "Point", coordinates: [lon, lat] },
      });
    }
    src.setData({ type: "FeatureCollection", features });
    if (now - this._pathAt > 1000) { this._pathAt = now; this._updatePath(); }
  }

  /** The rest of the selected vehicle's run, drawn bold on top of its line. */
  _updatePath() {
    const src = this.map.getSource(PATH_SRC);
    const p = this.selectedId && this.display.get(this.selectedId);
    if (!src) return;
    if (!p) { src.setData(EMPTY); return; }
    const line = this.getLine(p.lineId);
    const g = line.geom;
    const coords = [[p.lon, p.lat]];
    const ahead = (i) => (p.dir === 0 ? g.cum[i] > p.dist : g.cum[i] < p.dist);
    if (p.dir === 0) { for (let i = 0; i < g.coords.length; i++) if (ahead(i)) coords.push(g.coords[i]); }
    else { for (let i = g.coords.length - 1; i >= 0; i--) if (ahead(i)) coords.push(g.coords[i]); }
    if (coords.length < 2) { src.setData(EMPTY); return; }
    src.setData({ type: "FeatureCollection", features: [{ type: "Feature", properties: { color: line.color }, geometry: { type: "LineString", coordinates: coords } }] });
  }

  select(id) {
    this.selectedId = id;
    this._pathAt = 0;
  }

  pick(point, pad = 8) {
    if (!this.map.getLayer("veh-dot")) return null;
    const f = this.map.queryRenderedFeatures([[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]], { layers: ["veh-dot"] });
    return f.length ? f[0].properties.id : null;
  }
}
