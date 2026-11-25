export const minutes = (s) => Math.max(0, Math.round(s / 60));

/** "now" or "in 4 min" - for use inside a sentence. */
export function inTime(s) {
  return s < 30 ? "now" : s < 60 ? "in under a minute" : `in ${minutes(s)} min`;
}

export function eta(s) {
  if (s < 30) return "now";
  if (s < 60) return "<1 min";
  return `${minutes(s)} min`;
}

export function clock(date = new Date()) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function arrival(secondsFromNow) {
  return new Date(Date.now() + secondsFromNow * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function distance(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 10) * 10} m`;
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export const MODE_LABEL = { metro: "Metro", tram: "Tram", rail: "Rail", light_rail: "Light rail", bus: "Bus", ferry: "Ferry" };
