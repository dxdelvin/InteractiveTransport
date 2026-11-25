// Basemap styles in the "architectural model" look: pale stone ground, white massing,
// soft blue rivers. Colour is reserved for the transit lines drawn on top.
//
//   osm:        OpenFreeMap "liberty" vector tiles, desaturated into the model palette at load time.
//   procedural: offline fallback - the backend's schematic city (rivers, parks, blocks).
export const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
export const FONT = ["Noto Sans Regular"];
export const FONT_BOLD = ["Noto Sans Bold"];
const OSM_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

export const MODEL = {
  ground: "#dfe3e6",
  water: "#a9c9d8",
  waterEdge: "#8fb5c7",
  park: "#d2ddd1",
  road: "#f7f8f8",
  roadEdge: "#d3d8db",
  building: "#eef0f1",
  buildingTall: "#ffffff",
  label: "#4f5860",
};

export function proceduralStyle() {
  return {
    version: 8,
    name: "model-offline",
    glyphs: GLYPHS,
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": MODEL.ground } }],
  };
}

export function sceneryLayers() {
  return [
    { id: "sc-park", type: "fill", source: "scenery", filter: ["==", ["get", "kind"], "park"], paint: { "fill-color": MODEL.park } },
    { id: "sc-water", type: "fill", source: "scenery", filter: ["==", ["get", "kind"], "water"], paint: { "fill-color": MODEL.water } },
    { id: "sc-water-edge", type: "line", source: "scenery", filter: ["==", ["get", "kind"], "water"], paint: { "line-color": MODEL.waterEdge, "line-width": 1 } },
    {
      id: "sc-road", type: "line", source: "scenery", filter: ["==", ["get", "kind"], "road"], layout: { "line-cap": "round" },
      paint: {
        "line-color": MODEL.road,
        "line-width": ["interpolate", ["exponential", 1.6], ["zoom"], 12, ["case", ["get", "major"], 1.2, 0.5], 17, ["case", ["get", "major"], 16, 9]],
      },
    },
    {
      id: "sc-buildings", type: "fill-extrusion", source: "scenery", filter: ["==", ["get", "kind"], "building"], minzoom: 12,
      paint: {
        "fill-extrusion-color": ["interpolate", ["linear"], ["get", "height"], 8, MODEL.building, 40, MODEL.buildingTall],
        "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 12, 0, 13.5, ["get", "height"]],
        "fill-extrusion-opacity": 0.96,
        "fill-extrusion-vertical-gradient": true,
      },
    },
  ];
}

// ---- turning a third-party style into the model palette -----------------------------

function parseColor(str) {
  if (typeof str !== "string") return null;
  const s = str.trim().toLowerCase();
  let m;
  if ((m = s.match(/^#([0-9a-f]{3,8})$/))) {
    let h = m[1];
    if (h.length <= 4) h = [...h].map((c) => c + c).join("");
    const n = parseInt(h.slice(0, 6), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1];
  }
  if ((m = s.match(/^rgba?\(([^)]+)\)$/))) {
    const p = m[1].split(",").map(parseFloat);
    return [p[0], p[1], p[2], p[3] ?? 1];
  }
  if ((m = s.match(/^hsla?\(([^)]+)\)$/))) {
    const p = m[1].split(",").map(parseFloat);
    return [...hslToRgb(p[0] / 360, p[1] / 100, p[2] / 100), p[3] ?? 1];
  }
  return null;
}

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h /= 6;
  }
  return [h, s, l];
}

function hslToRgb(h, s, l) {
  if (s === 0) return [l * 255, l * 255, l * 255];
  const f = (p, q, t) => {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [f(p, q, h + 1 / 3) * 255, f(p, q, h) * 255, f(p, q, h - 1 / 3) * 255];
}

/** Push any colour into the near-monochrome stone palette, keeping its relative lightness. */
function modelColor(str, role) {
  const c = parseColor(str);
  if (!c) return str;
  const [h, s, l] = rgbToHsl(c[0], c[1], c[2]);
  const hue = 0.57; // the slightly blue grey of the ground
  let nl, ns = 0.06;
  if (role === "text") { nl = 0.32 + l * 0.12; ns = 0.08; }
  else if (role === "halo") { nl = 0.97; ns = 0.02; }
  else if (role === "line") { nl = 0.82 + l * 0.16; }
  else { nl = 0.84 + l * 0.08; ns = s * 0.12; }
  const [r, g, b] = hslToRgb(hue * 0.8 + h * 0.2, ns, Math.min(0.99, nl));
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${c[3]})`;
}

function recolor(value, role) {
  if (typeof value === "string") return modelColor(value, role);
  if (Array.isArray(value)) return value.map((v) => recolor(v, role));
  if (value && typeof value === "object" && Array.isArray(value.stops)) return { ...value, stops: value.stops.map(([z, v]) => [z, recolor(v, role)]) };
  return value;
}

export async function osmModelStyle() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  const res = await fetch(OSM_STYLE_URL, { signal: ctrl.signal }).finally(() => clearTimeout(timer));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const style = await res.json();
  // keep streets, water, parks and place names; drop the clutter that competes with the network
  style.layers = style.layers.filter((l) => l.type !== "fill-extrusion" && !(l.type === "symbol" && /poi|housenumber|aerialway|ferry|transit|railway|road_shield|highway-shield|oneway/.test(l.id)));
  for (const layer of style.layers) {
    const paint = (layer.paint ||= {});
    for (const key of Object.keys(paint)) {
      if (!key.endsWith("color")) continue;
      const role = key === "text-color" ? "text" : key === "text-halo-color" ? "halo" : layer.type === "line" ? "line" : "fill";
      paint[key] = recolor(paint[key], role);
    }
    if (layer.type === "background") paint["background-color"] = MODEL.ground;
    if (/water/.test(layer.id) && layer.type === "fill") paint["fill-color"] = MODEL.water;
    if (/water/.test(layer.id) && layer.type === "line") paint["line-color"] = MODEL.water;
    if (/park|wood|grass|landcover|landuse_park/.test(layer.id) && layer.type === "fill") paint["fill-color"] = MODEL.park;
    if (layer.type === "fill" && /building/.test(layer.id)) layer.layout = { ...(layer.layout || {}), visibility: "none" };
    if (layer.type === "symbol") paint["icon-opacity"] = 0;
  }
  style.layers.push({
    id: "osm-buildings", type: "fill-extrusion", source: "openmaptiles", "source-layer": "building", minzoom: 13,
    paint: {
      "fill-extrusion-color": ["interpolate", ["linear"], ["coalesce", ["get", "render_height"], 10], 8, MODEL.building, 40, MODEL.buildingTall],
      "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 13, 0, 14.5, ["coalesce", ["get", "render_height"], 10]],
      "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
      "fill-extrusion-opacity": 0.96,
      "fill-extrusion-vertical-gradient": true,
    },
  });
  return style;
}
