// Minimal app state + pub/sub. Components subscribe to named events.
const listeners = new Map();

export const state = {
  provider: null,        // ProviderInfo
  providers: [],
  network: null,         // { lines, stations, bbox }
  lines: new Map(),      // id -> line (with .geom)
  stations: new Map(),   // id -> station
  vehicles: new Map(),   // id -> latest Vehicle from the server
  selection: null,       // { kind: "vehicle"|"station", id }
  follow: null,          // null | "track" | "cab"
  focusLines: null,      // Set of line ids or null (= all)
  journeys: [],
  activeJourney: -1,
  origin: null,          // PlaceRef-like {station_id|lat,lon,label}
  destination: null,
  userLocation: null,    // {lat, lon}
  aiEngine: "local",
};

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, payload) {
  (listeners.get(event) || []).forEach((fn) => {
    try { fn(payload); } catch (e) { console.error(`listener for ${event} failed`, e); }
  });
}
