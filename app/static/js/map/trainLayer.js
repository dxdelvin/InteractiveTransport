// Three.js custom MapLibre layer that renders every vehicle as an articulated 3D train.
//
// * Each car is placed by *distance along its line* and oriented by the track tangent
//   at that distance, so trains bend through curves instead of sliding as rigid boxes.
// * Between server snapshots each vehicle glides from its last shown distance to the new
//   one (wrap-aware on loop lines), so motion stays smooth at any update rate.
// * Trains are exaggerated as you zoom out so they stay readable at city scale.
// * The layer shares MapLibre's depth buffer: buildings correctly hide trains behind them.
import * as THREE from "three";

const CONSIST = {
  metro: { cars: 4, len: 19, w: 3.0, h: 3.6 },
  light_rail: { cars: 2, len: 22, w: 2.7, h: 3.5 },
  tram: { cars: 3, len: 13.5, w: 2.65, h: 3.5 },
  rail: { cars: 6, len: 24, w: 3.1, h: 4.3 },
  bus: { cars: 1, len: 12, w: 2.6, h: 3.2 },
  ferry: { cars: 1, len: 32, w: 8, h: 4 },
};
const GAP = 1.6;
const MAX_CARS = 4000;
const rad = Math.PI / 180;

function radialTexture(inner = "rgba(255,255,255,0.9)") {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, inner);
  grd.addColorStop(0.45, "rgba(255,255,255,0.4)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function beamTexture() {
  const c = document.createElement("canvas");
  c.width = 4; c.height = 256;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 0, 0, 256);
  grd.addColorStop(0, "rgba(255,255,255,0)");
  grd.addColorStop(1, "rgba(255,255,255,0.9)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 4, 256);
  return new THREE.CanvasTexture(c);
}

export class TrainLayer {
  constructor({ getLine, onFrame }) {
    this.id = "trains";
    this.type = "custom";
    this.renderingMode = "3d";
    this.getLine = getLine;       // id -> { geom: LineGeometry, mode, color }
    this.onFrame = onFrame;       // called each frame (camera follow etc.)
    this.tracks = new Map();      // vehicle id -> interpolation state
    this.display = new Map();     // vehicle id -> { lon, lat, bearing, lineId }
    this.selectedId = null;
    this.focus = null;            // Set of highlighted line ids or null
    this.lastSnapshotAt = 0;
    this.interval = 1000;
    this._dummy = new THREE.Object3D();
    this._color = new THREE.Color();
    this._white = new THREE.Color("#ffffff");
    this._stone = new THREE.Color("#c9cfd3");
    this._glass = new THREE.Color("#22303a");
  }

  // ---------------------------------------------------------------- MapLibre hooks
  onAdd(map, gl) {
    this.map = map;
    this.camera = new THREE.Camera();
    this.scene = new THREE.Scene();

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a949b, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(-0.6, -0.4, 1).normalize();
    this.scene.add(sun);

    const box = new THREE.BoxGeometry(1, 1, 1);
    box.translate(0, 0, 0.5); // base sits on the ground plane
    const roundedTop = new THREE.BoxGeometry(1, 1, 1);
    roundedTop.translate(0, 0, 0.5);

    const body = new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 0.1, roughness: 0.55, side: THREE.DoubleSide });
    this.bodies = new THREE.InstancedMesh(box, body, MAX_CARS);

    const windows = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide, toneMapped: false });
    this.windows = new THREE.InstancedMesh(roundedTop, windows, MAX_CARS);

    const lamp = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
    this.lamps = new THREE.InstancedMesh(box, lamp, MAX_CARS);

    // soft contact shadow under each vehicle - grounds it on the pale model
    const glowMat = new THREE.MeshBasicMaterial({
      map: radialTexture(), transparent: true, opacity: 0.32, depthWrite: false, side: THREE.DoubleSide, toneMapped: false,
    });
    this.glows = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), glowMat, MAX_CARS);

    for (const m of [this.bodies, this.windows, this.lamps, this.glows]) {
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(MAX_CARS * 3), 3);
      m.instanceColor.setUsage(THREE.DynamicDrawUsage);
      m.frustumCulled = false;
      m.count = 0;
    }
    this.glows.renderOrder = -1;
    this.scene.add(this.glows, this.bodies, this.windows, this.lamps);

    // selection marker: pulsing ring + light beam
    this.ring = new THREE.Mesh(
      new THREE.RingGeometry(0.82, 1, 72),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, depthWrite: false, side: THREE.DoubleSide }),
    );
    const beamGeo = new THREE.CylinderGeometry(1, 1, 1, 32, 1, true);
    beamGeo.rotateX(Math.PI / 2);
    beamGeo.translate(0, 0, 0.5);
    this.beam = new THREE.Mesh(
      beamGeo,
      new THREE.MeshBasicMaterial({ map: beamTexture(), color: 0xffffff, transparent: true, opacity: 0.7, depthWrite: false, side: THREE.DoubleSide }),
    );
    this.ring.visible = this.beam.visible = false;
    this.scene.add(this.ring, this.beam);

    this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl, antialias: true });
    this.renderer.autoClear = false;
  }

  onRemove() {
    // The renderer shares MapLibre's GL context, so it must not be disposed here:
    // that would tear down state MapLibre still uses. A new one is made on re-add.
    this.renderer = null;
  }

  setOrigin(lngLat) {
    const mc = maplibregl.MercatorCoordinate.fromLngLat(lngLat, 0);
    this.ox = mc.x;
    this.oy = mc.y;
    this.s = mc.meterInMercatorCoordinateUnits();
    this.origin = new THREE.Matrix4().makeTranslation(this.ox, this.oy, 0).scale(new THREE.Vector3(this.s, -this.s, this.s));
  }

  toLocal(lon, lat) {
    const mc = maplibregl.MercatorCoordinate.fromLngLat([lon, lat], 0);
    return [(mc.x - this.ox) / this.s, -(mc.y - this.oy) / this.s];
  }

  // ---------------------------------------------------------------- data in
  clear() {
    this.tracks.clear();
    this.display.clear();
    this.lastSnapshotAt = 0;
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
        this.tracks.set(v.id, { lineId: v.line_id, dir: v.direction, from: v.shape_dist_m, to: v.shape_dist_m, t0: now, dur: 1, status: v.status });
        continue;
      }
      const current = this._distAt(tr, now, line.geom);
      const jump = Math.abs(line.geom.delta(current, v.shape_dist_m));
      if (jump > 2500) {
        Object.assign(tr, { from: v.shape_dist_m, to: v.shape_dist_m, t0: now, dur: 1 });
      } else {
        Object.assign(tr, { from: current, to: current + line.geom.delta(current, v.shape_dist_m), t0: now, dur: this.interval * 1.05 });
      }
      tr.status = v.status;
    }
    for (const id of this.tracks.keys()) if (!seen.has(id)) { this.tracks.delete(id); this.display.delete(id); }
  }

  _distAt(tr, now, geom) {
    const f = Math.min(1, Math.max(0, (now - tr.t0) / tr.dur));
    return geom.wrap(tr.from + (tr.to - tr.from) * f);
  }

  // ---------------------------------------------------------------- frame
  /** Metres per screen pixel at the map centre (MapLibre uses 512px tiles). */
  _metersPerPixel() {
    const lat = this.map.getCenter().lat;
    return (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * Math.pow(2, this.map.getZoom()));
  }

  render(gl, matrix) {
    if (this.ox === undefined) return;
    const now = performance.now();
    // Real dimensions up close; a minimum on-screen size when zoomed out so trains
    // always read as trains rather than specks on the line.
    const mpp = this._metersPerPixel();
    const k = Math.max(1, (12 * mpp) / 19);
    const d = this._dummy;
    const col = this._color;
    let n = 0, lampN = 0, glowN = 0;
    const pulse = 0.5 + 0.5 * Math.sin(now / 260);

    for (const [id, tr] of this.tracks) {
      const line = this.getLine(tr.lineId);
      if (!line) continue;
      const geom = line.geom;
      const spec = CONSIST[line.mode] || CONSIST.metro;
      const sign = tr.dir === 0 ? 1 : -1;
      const carLen = Math.max(spec.len, spec.len * k);
      const pitch = carLen + GAP * k;
      const total = spec.cars * pitch;
      let head = this._distAt(tr, now, geom);
      if (!geom.loop) {
        if (sign > 0 && head - total < 0) head = Math.min(geom.length, total);
        if (sign < 0 && head + total > geom.length) head = Math.max(0, geom.length - total);
      }
      const dim = this.focus && !this.focus.has(tr.lineId);
      const selected = id === this.selectedId;
      const base = col.set(line.color);
      const bodyCol = dim ? base.clone().lerp(this._stone, 0.8) : base.clone();
      const winCol = dim ? this._stone.clone() : this._glass.clone().lerp(base, selected ? 0.35 : 0.12);
      const w = Math.max(spec.w * 1.8, 8 * mpp), h = Math.max(spec.h * 1.5, 6.5 * mpp);
      const lift = 0.3 * mpp;
      let headLL = null;

      for (let c = 0; c < spec.cars && n < MAX_CARS; c++) {
        const center = head - sign * (c * pitch + carLen / 2);
        const [lon, lat, brg] = geom.at(center);
        const heading = tr.dir === 0 ? brg : brg + 180;
        const [x, y] = this.toLocal(lon, lat);
        const rot = -heading * rad;
        if (c === 0) headLL = [lon, lat, heading];

        d.position.set(x, y, lift);
        d.rotation.set(0, 0, rot);
        d.scale.set(w, carLen, h);
        d.updateMatrix();
        this.bodies.setMatrixAt(n, d.matrix);
        this.bodies.setColorAt(n, bodyCol);

        // glowing window band
        d.position.set(x, y, lift + h * 0.48);
        d.scale.set(w * 1.04, carLen * 0.9, h * 0.22);
        d.updateMatrix();
        this.windows.setMatrixAt(n, d.matrix);
        this.windows.setColorAt(n, winCol);
        n++;

        // head- and tail-lights
        if ((c === 0 || c === spec.cars - 1) && lampN < MAX_CARS && !dim) {
          const front = c === 0;
          const off = (carLen / 2) * (front ? 1 : -1);
          const hx = x + Math.sin(heading * rad) * off;
          const hy = y + Math.cos(heading * rad) * off;
          d.position.set(hx, hy, lift + h * 0.25);
          d.scale.set(w * 0.8, Math.max(0.6, mpp * 1.2), h * 0.18);
          d.updateMatrix();
          this.lamps.setMatrixAt(lampN, d.matrix);
          this.lamps.setColorAt(lampN, front ? col.set("#fff6d6") : col.set("#ff2a3d"));
          lampN++;
        }
      }

      // light pool under the train
      if (headLL && glowN < MAX_CARS) {
        const mid = head - sign * (total / 2);
        const [lon, lat, brg] = geom.at(mid);
        const [x, y] = this.toLocal(lon, lat);
        d.position.set(x, y, 0.2);
        d.rotation.set(0, 0, -brg * rad);
        const glowW = Math.max(selected ? 60 : 36, (selected ? 34 : 20) * mpp);
        d.scale.set(glowW, total + glowW, 1);
        d.updateMatrix();
        this.glows.setMatrixAt(glowN, d.matrix);
        this.glows.setColorAt(glowN, selected ? col.set(line.color) : col.set(dim ? "#9aa3a9" : "#2a3338"));
        glowN++;
        this.display.set(id, { lon: headLL[0], lat: headLL[1], bearing: headLL[2], lineId: tr.lineId, mid: [lon, lat] });

        if (selected) {
          const r = Math.max(total * 0.75, 40 * mpp) * (1 + pulse * 0.15);
          this.ring.position.set(x, y, 0.5);
          this.ring.scale.set(r, r, 1);
          this.ring.material.color.set(line.color);
          this.ring.material.opacity = 0.5 + pulse * 0.4;
          this.beam.position.set(x, y, 0);
          this.beam.scale.set(3 * mpp, 3 * mpp, 260 * mpp);
          this.beam.material.color.set(line.color);
          this.ring.visible = this.beam.visible = true;
        }
      }
    }
    if (!this.selectedId || !this.tracks.has(this.selectedId)) this.ring.visible = this.beam.visible = false;

    for (const [m, c] of [[this.bodies, n], [this.windows, n], [this.lamps, lampN], [this.glows, glowN]]) {
      m.count = c;
      m.instanceMatrix.needsUpdate = true;
      if (m.instanceColor) m.instanceColor.needsUpdate = true;
    }

    this.onFrame?.();

    this.camera.projectionMatrix = new THREE.Matrix4().fromArray(matrix).multiply(this.origin);
    // Three.js and MapLibre share one GL context. Render into MapLibre's current viewport and
    // put it back afterwards: MapLibre caches GL state, and a stale viewport (e.g. after the
    // canvas is resized) would make the whole map draw into the wrong rectangle.
    const vp = gl.getParameter(gl.VIEWPORT);
    this.renderer.setPixelRatio(1);
    this.renderer.setViewport(vp[0], vp[1], vp[2], vp[3]);
    this.renderer.resetState();
    // Trains are the primary information, so - like the line strokes - they draw on top of
    // the skyline instead of disappearing behind towers. Clearing depth also keeps station
    // dots drawn after this layer visible above the 3D station towers.
    this.renderer.clearDepth();
    this.renderer.render(this.scene, this.camera);
    this.renderer.resetState();
    gl.viewport(vp[0], vp[1], vp[2], vp[3]);
    this.map.triggerRepaint();
  }

  /** Nearest vehicle to a screen point, or null. */
  pick(point, radiusPx = 24) {
    let best = null, bestD = radiusPx;
    for (const [id, p] of this.display) {
      const s = this.map.project([p.mid[0], p.mid[1]]);
      const dd = Math.hypot(s.x - point.x, s.y - point.y);
      if (dd < bestD) { bestD = dd; best = id; }
    }
    return best;
  }
}
