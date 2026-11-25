// Geometry shared by the map and the 3D train layer.
// Distances use the same haversine formula as the backend, so a vehicle's
// `shape_dist_m` lands on exactly the same spot here.
const R = 6371008.8;
const rad = Math.PI / 180;

export function haversine(a, b) {
  const dLat = (b[1] - a[1]) * rad;
  const dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function bearing(a, b) {
  const [l1, p1] = [a[0] * rad, a[1] * rad];
  const [l2, p2] = [b[0] * rad, b[1] * rad];
  const y = Math.sin(l2 - l1) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(l2 - l1);
  return ((Math.atan2(y, x) / rad) + 360) % 360;
}

/** A line shape with cumulative distances for O(log n) lookup by distance. */
export class LineGeometry {
  constructor(coords, loop = false) {
    this.coords = coords;
    this.loop = loop;
    this.cum = new Float64Array(coords.length);
    for (let i = 1; i < coords.length; i++) this.cum[i] = this.cum[i - 1] + haversine(coords[i - 1], coords[i]);
    this.length = this.cum[coords.length - 1];
  }

  wrap(d) {
    if (this.loop) return ((d % this.length) + this.length) % this.length;
    return Math.min(Math.max(d, 0), this.length);
  }

  /** -> [lon, lat, bearingDeg] */
  at(distance) {
    const d = this.wrap(distance);
    let lo = 0, hi = this.cum.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (this.cum[mid] <= d) lo = mid; else hi = mid;
    }
    const a = this.coords[lo], b = this.coords[hi];
    const seg = this.cum[hi] - this.cum[lo];
    const t = seg > 0 ? (d - this.cum[lo]) / seg : 0;
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, bearing(a, b)];
  }

  /** Signed shortest delta from a to b along the line (handles loop wrap-around). */
  delta(a, b) {
    let d = b - a;
    if (this.loop) {
      const L = this.length;
      d = ((d + L / 2) % L + L) % L - L / 2;
    }
    return d;
  }
}

export function bboxOf(coords) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [x, y] of coords) { if (x < w) w = x; if (x > e) e = x; if (y < s) s = y; if (y > n) n = y; }
  return [[w, s], [e, n]];
}
