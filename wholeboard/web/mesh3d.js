"use strict";
// A small 3D viewer for the enclosure preview: flat-shaded triangles drawn back to front on a canvas.
// Drag to orbit, wheel to zoom. No libraries, so it works offline and on any machine on the LAN.

class MeshView {
  constructor(host, data) {
    this.host = host;
    this.data = data;
    this.yaw = -0.7; this.pitch = 0.75; this.zoom = 1; this.explode = 0.6;
    this.show = { base: true, lid: true, board: true };
    this.canvas = document.createElement("canvas");
    this.canvas.className = "mesh-canvas";
    host.append(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    const [sx, sy, sz] = data.size;
    this.center = [sx / 2, sy / 2, sz / 2];
    this.radius = Math.hypot(sx, sy, sz) / 2;
    this.meshes = data.meshes.map(m => this._prep(m));
    this._bind();
    this.ro = new ResizeObserver(() => this._resize());
    this.ro.observe(host);
    this._onTheme = () => this.draw();
    window.addEventListener("wb-theme", this._onTheme);
    this._onZoom = () => this._resize();
    window.addEventListener("wb-zoom", this._onZoom);
  }

  _prep(m) {
    const v = Float32Array.from(m.v), t = Uint32Array.from(m.t), n = t.length / 3;
    const normals = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const a = t[3 * i] * 3, b = t[3 * i + 1] * 3, c = t[3 * i + 2] * 3;
      const ux = v[b] - v[a], uy = v[b + 1] - v[a + 1], uz = v[b + 2] - v[a + 2];
      const wx = v[c] - v[a], wy = v[c + 1] - v[a + 1], wz = v[c + 2] - v[a + 2];
      let nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
      const l = Math.hypot(nx, ny, nz) || 1;
      normals[3 * i] = nx / l; normals[3 * i + 1] = ny / l; normals[3 * i + 2] = nz / l;
    }
    const hex = m.color.replace("#", "");
    const rgb = [0, 2, 4].map(k => parseInt(hex.slice(k, k + 2), 16));
    return { ...m, v, t, n, normals, rgb };
  }

  _resize() {
    // layout pixels; the backing store also covers the page zoom so it stays sharp
    this.dpr = (window.devicePixelRatio || 1) * wbZoom();
    this.w = this.host.clientWidth; this.h = this.host.clientHeight;
    this.canvas.width = Math.max(1, this.w * this.dpr);
    this.canvas.height = Math.max(1, this.h * this.dpr);
    this.canvas.style.width = this.w + "px"; this.canvas.style.height = this.h + "px";
    this.draw();
  }

  _bind() {
    let drag = null;
    this.canvas.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY]; this.canvas.setPointerCapture(e.pointerId); });
    this.canvas.addEventListener("pointermove", e => {
      if (!drag) return;
      this.yaw += (e.clientX - drag[0]) * 0.01;
      this.pitch = Math.max(-1.55, Math.min(1.55, this.pitch + (e.clientY - drag[1]) * 0.01));
      drag = [e.clientX, e.clientY];
      this.draw();
    });
    this.canvas.addEventListener("pointerup", () => { drag = null; });
    this.canvas.addEventListener("wheel", e => {
      e.preventDefault();
      this.zoom = Math.max(0.3, Math.min(6, this.zoom * Math.exp(-e.deltaY * 0.0015)));
      this.draw();
    }, { passive: false });
  }

  draw() {
    const c = this.ctx;
    if (!c || !this.w) return;
    // Rasterise with a depth buffer (painter's sorting fails on long, thin wall faces).
    const ss = this.dpr < 2 ? 2 : 1;                       // supersample on low-density screens
    const W = Math.round(this.w * this.dpr * ss), H = Math.round(this.h * this.dpr * ss);
    if (!this.off || this.off.width !== W || this.off.height !== H) {
      this.off = document.createElement("canvas"); this.off.width = W; this.off.height = H;
      this.offCtx = this.off.getContext("2d");
      this.img = this.offCtx.createImageData(W, H);
      this.px = new Uint32Array(this.img.data.buffer);
      this.zb = new Float32Array(W * H);
    }
    const px = this.px, zb = this.zb;
    const bg = parseColour(getComputedStyle(document.documentElement).getPropertyValue("--panel-2")) || [34, 34, 34];
    px.fill((255 << 24) | (bg[2] << 16) | (bg[1] << 8) | bg[0]);
    zb.fill(-Infinity);
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const scale = Math.min(W, H) / (2.4 * this.radius) * this.zoom;
    const lift = this.explode * this.data.size[2];
    const [ox, oy, oz] = this.center;
    const L = [-0.35, 0.45, 0.82];
    for (const m of this.meshes) {
      if (!this.show[m.group]) continue;
      const dz = m.group === "lid" ? lift : 0;
      const v = m.v, nv = v.length / 3, P = new Float32Array(nv * 3);
      // World Z is up. Turn about Z (yaw), then tilt about the screen's X axis (pitch).
      for (let i = 0; i < nv; i++) {
        const x = v[3 * i] - ox, y = v[3 * i + 1] - oy, z = v[3 * i + 2] + dz - oz;
        const x1 = x * cy - y * sy, y1 = x * sy + y * cy;
        P[3 * i] = W / 2 + x1 * scale;
        P[3 * i + 1] = H / 2 - (y1 * sp + z * cp) * scale;
        P[3 * i + 2] = -y1 * cp + z * sp;                   // towards the viewer
      }
      for (let i = 0; i < m.n; i++) {
        const nx = m.normals[3 * i], ny = m.normals[3 * i + 1], nz = m.normals[3 * i + 2];
        const nx1 = nx * cy - ny * sy, ny1 = nx * sy + ny * cy;
        const nsy = ny1 * sp + nz * cp, nd = -ny1 * cp + nz * sp;
        if (nd < -1e-3) continue;                           // facing away
        const lit = 0.35 + 0.65 * Math.max(0, nx1 * L[0] + nsy * L[1] + nd * L[2]);
        const col = (255 << 24) | ((m.rgb[2] * lit | 0) << 16) | ((m.rgb[1] * lit | 0) << 8) | (m.rgb[0] * lit | 0);
        const a = m.t[3 * i] * 3, b = m.t[3 * i + 1] * 3, cc = m.t[3 * i + 2] * 3;
        tri(P[a], P[a + 1], P[a + 2], P[b], P[b + 1], P[b + 2], P[cc], P[cc + 1], P[cc + 2], col, px, zb, W, H);
      }
    }
    this.offCtx.putImageData(this.img, 0, 0);
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.imageSmoothingEnabled = true; c.imageSmoothingQuality = "high";
    c.drawImage(this.off, 0, 0, this.canvas.width, this.canvas.height);
  }
}

function tri(x0, y0, z0, x1, y1, z1, x2, y2, z2, col, px, zb, W, H) {
  const area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
  if (Math.abs(area) < 1e-9) return;
  const minX = Math.max(0, Math.floor(Math.min(x0, x1, x2))), maxX = Math.min(W - 1, Math.ceil(Math.max(x0, x1, x2)));
  const minY = Math.max(0, Math.floor(Math.min(y0, y1, y2))), maxY = Math.min(H - 1, Math.ceil(Math.max(y0, y1, y2)));
  const inv = 1 / area;
  for (let y = minY; y <= maxY; y++) {
    const py = y + 0.5;
    for (let x = minX; x <= maxX; x++) {
      const pxc = x + 0.5;
      const w0 = ((x1 - pxc) * (y2 - py) - (x2 - pxc) * (y1 - py)) * inv;
      if (w0 < -1e-6) continue;
      const w1 = ((x2 - pxc) * (y0 - py) - (x0 - pxc) * (y2 - py)) * inv;
      if (w1 < -1e-6) continue;
      const w2 = 1 - w0 - w1;
      if (w2 < -1e-6) continue;
      const z = w0 * z0 + w1 * z1 + w2 * z2, k = y * W + x;
      if (z > zb[k]) { zb[k] = z; px[k] = col; }
    }
  }
}

function parseColour(s) {
  s = (s || "").trim();
  let m = s.match(/^#([0-9a-f]{3})$/i);
  if (m) return [...m[1]].map(h => parseInt(h + h, 16));
  m = s.match(/^#([0-9a-f]{6})/i);
  if (m) return [0, 2, 4].map(k => parseInt(m[1].slice(k, k + 2), 16));
  m = s.match(/rgba?\(([^)]+)\)/);
  if (m) return m[1].split(",").slice(0, 3).map(x => parseInt(x));
  return null;
}
window.MeshView = MeshView;
