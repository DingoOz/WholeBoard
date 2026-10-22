"use strict";
// Sim Lab: the simulation drawn on the finished board in 3D. Current flows along the tracks, LEDs glow as
// bright as the simulation says, and a heat map shows how the board warms up. Plain WebGL, no libraries, so it
// works offline. With an enclosure the board can sit in its box: the server works out the inside air, and the
// box is drawn see-through, coloured by its surface temperatures. The heat map is solved here: a 2D plate (FR4 plus copper) that spreads each part's heat and
// loses it to the air from both faces.

const SL_HEAT = [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]];
const SL_FLOW = { power: "#ffb347", ground: "#5fb8ff", signal: "#7dffb0" };
const SL_LEVEL = { problem: "#ff5a52", warning: "#ffb020", watch: "#5fb8ff" };

function slHeat(t) {
  t = Math.max(0, Math.min(1, t)) * (SL_HEAT.length - 1);
  const i = Math.min(SL_HEAT.length - 2, Math.floor(t)), f = t - i, a = SL_HEAT[i], b = SL_HEAT[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

// ---------------------------------------------------------------- small matrix helpers (column-major)
const M4 = {
  mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      o[c * 4 + r] = s;
    }
    return o;
  },
  persp(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0]);
  },
  lookAt(e, t, u) {
    let zx = e[0] - t[0], zy = e[1] - t[1], zz = e[2] - t[2];
    let l = Math.hypot(zx, zy, zz); zx /= l; zy /= l; zz /= l;
    let xx = u[1] * zz - u[2] * zy, xy = u[2] * zx - u[0] * zz, xz = u[0] * zy - u[1] * zx;
    l = Math.hypot(xx, xy, xz); xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    return new Float32Array([xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0,
      -(xx * e[0] + xy * e[1] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1]);
  },
  box(x0, y0, z0, x1, y1, z1) {     // scale and translate a unit cube (0..1) to this box
    return new Float32Array([x1 - x0, 0, 0, 0, 0, y1 - y0, 0, 0, 0, 0, z1 - z0, 0, x0, y0, z0, 1]);
  },
  invert(m) {
    const inv = new Float32Array(16);
    inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
    inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
    inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
    inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
    inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
    inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
    inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
    inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
    inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
    inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
    inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
    inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
    inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
    inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
    inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
    inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
    let det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
    det = det ? 1 / det : 0;
    for (let i = 0; i < 16; i++) inv[i] *= det;
    return inv;
  },
};

// ---------------------------------------------------------------- thermal model
class SlThermal {
  // A plate on a grid: each cell conducts heat to its neighbours through FR4 and whatever copper covers it,
  // and loses it to the air from both faces. Solved by successive over-relaxation, a few hundred sweeps.
  constructor(scene) {
    const B = scene.board;
    this.dx = Math.max(B.w, B.h) / 140;
    this.nx = Math.max(4, Math.ceil(B.w / this.dx)); this.ny = Math.max(4, Math.ceil(B.h / this.dx));
    this.scene = scene;
    const n = this.nx * this.ny;
    this.mask = this._raster(ctx => { ctx.beginPath(); scene.outline.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.fill();
      ctx.globalCompositeOperation = "destination-out";
      for (const h of scene.holes) { ctx.beginPath(); ctx.arc(h.x, h.y, h.d / 2, 0, 7); ctx.fill(); }
      for (const p of scene.pads) if (p.drill > 1) { ctx.beginPath(); ctx.arc(p.x, p.y, p.drill / 2, 0, 7); ctx.fill(); } });
    this.cover = ["F.Cu", "B.Cu"].map(layer => this._raster(ctx => {
      for (const z of scene.zones) if (z.layer === layer) { ctx.beginPath(); z.pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.fill(); }
      ctx.lineCap = "round";
      for (const t of scene.tracks) if (t.layer === layer) { ctx.lineWidth = t.w; ctx.beginPath(); ctx.moveTo(t.a[0], t.a[1]); ctx.lineTo(t.b[0], t.b[1]); ctx.stroke(); }
      for (const p of scene.pads) if (layer === "F.Cu" ? p.front : p.back) { ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(-p.rot * Math.PI / 180); ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore(); }
    }));
    this.theta = new Float32Array(n);
    this.q = new Float32Array(n);
    this.settings = { airflow: 12, copper: 1, ambient: 25 };
    this.power = {};
    this.thetaJb = {};
    this.dirty = true;
  }

  _raster(draw) {
    const c = document.createElement("canvas");
    c.width = this.nx; c.height = this.ny;
    const ctx = c.getContext("2d");
    ctx.scale(1 / this.dx, 1 / this.dx);
    ctx.fillStyle = ctx.strokeStyle = "#fff";
    draw(ctx);
    const d = ctx.getImageData(0, 0, this.nx, this.ny).data, out = new Float32Array(this.nx * this.ny);
    for (let i = 0; i < out.length; i++) out[i] = d[i * 4 + 3] / 255;
    return out;
  }

  setup() {
    // conductance of each cell as a sheet (W/K), and the links between cells
    const { nx, ny } = this, n = nx * ny, s = this.settings, B = this.scene.board;
    const tCu = 35e-6 * s.copper, inner = Math.max(0, B.layers - 2) * 385 * 17.5e-6 * 0.8;
    const G = new Float32Array(n);
    for (let i = 0; i < n; i++) G[i] = this.mask[i] > 0.5 ? 0.8 * B.t * 1e-3 + 385 * tCu * (this.cover[0][i] + this.cover[1][i]) + inner : 0;
    const area = (this.dx * 1e-3) ** 2, h = s.airflow * area * 1.7;   // the underside sheds about 70% of the top
    this.g = new Float32Array(n * 4); this.den = new Float32Array(n);
    for (let y = 0; y < ny; y++) for (let x = 0; x < nx; x++) {
      const i = y * nx + x;
      if (!G[i]) continue;
      const nb = [x > 0 ? i - 1 : -1, x < nx - 1 ? i + 1 : -1, y > 0 ? i - nx : -1, y < ny - 1 ? i + nx : -1];
      let d = h * this.mask[i];
      nb.forEach((j, k) => { const g = j >= 0 && G[j] ? 2 * G[i] * G[j] / (G[i] + G[j]) : 0; this.g[i * 4 + k] = g; d += g; });
      this.den[i] = d;
    }
    // heat sources: each part's dissipation spread over its body
    this.q.fill(0);
    this.cells = {};
    for (const p of this.scene.parts) {
      const x0 = Math.max(0, Math.floor(p.box[0] / this.dx)), x1 = Math.min(nx - 1, Math.floor(p.box[2] / this.dx));
      const y0 = Math.max(0, Math.floor(p.box[1] / this.dx)), y1 = Math.min(ny - 1, Math.floor(p.box[3] / this.dx));
      const cells = [];
      for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) if (this.den[y * nx + x]) cells.push(y * nx + x);
      this.cells[p.ref] = cells;
      const w = (this.power[p.ref] || 0) / 1000;
      if (w && cells.length) for (const i of cells) this.q[i] += w / cells.length;
    }
    let qt = 0, ht = 0;
    for (let i = 0; i < n; i++) if (this.den[i]) { qt += this.q[i]; ht += h * this.mask[i]; }
    if (!this.warm && ht) { this.theta.fill(qt / ht); this.warm = true; }
    this.iter = 0; this.converged = false; this.dirty = false;
  }

  step(ms = 10) {
    if (this.dirty) this.setup();
    if (this.converged) return false;
    const { nx, ny } = this, n = nx * ny, T = this.theta, g = this.g, den = this.den, q = this.q, w = 1.9;
    const t0 = performance.now();
    while (performance.now() - t0 < ms) {
      let maxd = 0, maxT = 0;
      for (let k = 0; k < 4; k++) {
        for (let i = 0; i < n; i++) {
          if (!den[i]) continue;
          const x = i % nx, y = (i / nx) | 0;
          let s = q[i];
          if (x > 0) s += g[i * 4] * T[i - 1];
          if (x < nx - 1) s += g[i * 4 + 1] * T[i + 1];
          if (y > 0) s += g[i * 4 + 2] * T[i - nx];
          if (y < ny - 1) s += g[i * 4 + 3] * T[i + nx];
          const v = T[i] + w * (s / den[i] - T[i]);
          maxd = Math.max(maxd, Math.abs(v - T[i])); maxT = Math.max(maxT, v);
          T[i] = v;
        }
      }
      this.iter += 4;
      if (maxd < Math.max(1e-4, maxT * 2e-5) || this.iter > 6000) { this.converged = true; break; }
    }
    return true;
  }

  // board temperature under a part plus its own rise from case to board
  partTemp(ref) {
    const cells = this.cells?.[ref];
    if (!cells?.length) return null;
    let s = 0;
    for (const i of cells) s += this.theta[i];
    return this.settings.ambient + s / cells.length + (this.power[ref] || 0) / 1000 * (this.thetaJb[ref] || 0);
  }

  minTemp() {
    let m = Infinity;
    for (let i = 0; i < this.theta.length; i++) if (this.den[i]) m = Math.min(m, this.theta[i]);
    return this.settings.ambient + (m === Infinity ? 0 : m);
  }

  maxTemp() {
    let m = 0;
    for (let i = 0; i < this.theta.length; i++) if (this.den[i]) m = Math.max(m, this.theta[i]);
    return this.settings.ambient + m;
  }
}

// ---------------------------------------------------------------- the 3D view
class SimLab {
  constructor(host, scene, opts = {}) {
    this.host = host; this.scene = scene; this.opts = opts;
    this.show = { flow: true, heat: false, glow: true, labels: false, box: !!scene.box_heat?.on };
    this.yaw = -0.35; this.pitch = 0.95; this.dist = 1; this.target = [scene.board.w / 2, 0, scene.board.h / 2];
    this.anim = null; this.caption = null; this.sel = null; this.hover = null; this.focusRefs = []; this.riskLevel = null;
    this.parts = Object.fromEntries(scene.parts.map(p => [p.ref, p]));
    this.wrap = document.createElement("div"); this.wrap.className = "simlab-view";
    this.gl3 = document.createElement("canvas"); this.over = document.createElement("canvas");
    this.wrap.append(this.gl3, this.over); host.append(this.wrap);
    this.gl = this.gl3.getContext("webgl", { antialias: true, preserveDrawingBuffer: true });
    if (!this.gl) { host.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "This browser has no WebGL, so the 3D Sim Lab cannot be shown." })); return; }
    this.octx = this.over.getContext("2d");
    this.ppm = Math.min(24, 2048 / Math.max(scene.board.w, scene.board.h));
    this.tex = document.createElement("canvas");
    this.tex.width = Math.ceil(scene.board.w * this.ppm); this.tex.height = Math.ceil(scene.board.h * this.ppm);
    this.tctx = this.tex.getContext("2d");
    this.heatImg = document.createElement("canvas");
    this.thermal = new SlThermal(scene);
    this._initGL();
    this.update(scene);
    this._bind();
    this.ro = new ResizeObserver(() => this._resize()); this.ro.observe(host);
    this._onTheme = () => { this.texDirty = true; };
    window.addEventListener("wb-theme", this._onTheme);
    this.t0 = performance.now();
    const loop = () => {
      if (!this.host.isConnected) { this.destroy(); return; }
      this._frame();
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.ro?.disconnect();
    window.removeEventListener("wb-theme", this._onTheme);
    if (this.rec?.state === "recording") this.rec.stop();
  }

  // new simulation numbers: part heat, LED light, currents and risks
  update(dyn) {
    this.dyn = dyn;
    this.thermal.power = Object.fromEntries(Object.entries(dyn.state).map(([r, s]) => [r, s.power_mw || 0]));
    this.thermal.thetaJb = Object.fromEntries(Object.entries(dyn.state).map(([r, s]) => [r, s.theta_jb || 0]));
    if (dyn.ambient_c != null) this.thermal.settings.ambient = dyn.ambient_c;
    // inside a box the board's faces lose heat only to the still air and the walls round it
    const bh = dyn.box_heat;
    this.thermal.settings.airflow = bh?.on ? bh.board_h : (this.userAirflow ?? 12);
    if (bh?.on !== this._boxOn) { this._boxOn = bh?.on; this.show.box = !!bh?.on; }
    this.thermal.dirty = true;
    this.maxMa = Math.max(1, ...Object.values(dyn.net_ma || {}));
    this.texDirty = true;
  }

  setLocal(key, value) {
    if (key === "airflow") { this.userAirflow = value; if (this.dyn?.box_heat?.on) return; }
    this.thermal.settings[key] = value;
    this.thermal.dirty = true;
  }

  // ------------------------------------------------------------ GL setup
  _initGL() {
    const gl = this.gl;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, `attribute vec3 p; attribute vec3 n; attribute vec2 uv; uniform mat4 mvp; uniform mat4 model;
      varying vec3 vn; varying vec2 vuv; void main() { vn = mat3(model) * n; vuv = uv; gl_Position = mvp * vec4(p, 1.0); }`));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, `precision mediump float; varying vec3 vn; varying vec2 vuv;
      uniform vec4 col; uniform sampler2D tex; uniform float useTex; uniform vec3 L; uniform vec3 emis; uniform float op;
      void main() { vec4 c = useTex > 0.5 ? texture2D(tex, vuv) : col; if (c.a < 0.5) discard;
        float d = abs(dot(normalize(vn), L)); gl_FragColor = vec4(c.rgb * (0.42 + 0.62 * d) + emis, op); }`));
    gl.linkProgram(prog); gl.useProgram(prog);
    this.prog = prog;
    this.loc = Object.fromEntries(["mvp", "model", "col", "tex", "useTex", "L", "emis", "op"].map(k => [k, gl.getUniformLocation(prog, k)]));
    this.attr = Object.fromEntries(["p", "n", "uv"].map(k => [k, gl.getAttribLocation(prog, k)]));
    const B = this.scene.board;
    // a unit cube: 6 faces with normals, no uv
    const cube = [];
    const face = (n, pts) => { for (const i of [0, 1, 2, 0, 2, 3]) cube.push(...pts[i], ...n, 0, 0); };
    face([0, 1, 0], [[0, 1, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1]]); face([0, -1, 0], [[0, 0, 0], [0, 0, 1], [1, 0, 1], [1, 0, 0]]);
    face([1, 0, 0], [[1, 0, 0], [1, 0, 1], [1, 1, 1], [1, 1, 0]]); face([-1, 0, 0], [[0, 0, 0], [0, 1, 0], [0, 1, 1], [0, 0, 1]]);
    face([0, 0, 1], [[0, 0, 1], [0, 1, 1], [1, 1, 1], [1, 0, 1]]); face([0, 0, -1], [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]);
    this.cube = this._buf(cube);
    const top = [], bot = [];
    const q = (arr, y, ny) => { for (const [x, z] of [[0, 0], [B.w, 0], [B.w, B.h], [0, 0], [B.w, B.h], [0, B.h]]) arr.push(x, y, z, 0, ny, 0, x / B.w, z / B.h); };
    q(top, 0, 1); q(bot, -B.t, -1);
    this.top = this._buf(top); this.bot = this._buf(bot);
    const wall = [], o = this.scene.outline;
    for (let i = 0; i < o.length; i++) {
      const a = o[i], b = o[(i + 1) % o.length], nx = b[1] - a[1], nz = -(b[0] - a[0]);
      for (const [p, y] of [[a, 0], [b, 0], [b, -B.t], [a, 0], [b, -B.t], [a, -B.t]]) wall.push(p[0], y, p[1], nx, 0, nz, 0, 0);
    }
    this.wall = this._buf(wall);
    this.glTex = gl.createTexture();
    this.glBotTex = gl.createTexture();
    gl.enable(gl.DEPTH_TEST);
  }

  _buf(data) {
    const gl = this.gl, b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    return { b, n: data.length / 8 };
  }

  _texUpload(tex, canvas) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  _draw(buf, mvp, model, opts) {
    const gl = this.gl, L = this.loc;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf.b);
    gl.enableVertexAttribArray(this.attr.p); gl.vertexAttribPointer(this.attr.p, 3, gl.FLOAT, false, 32, 0);
    gl.enableVertexAttribArray(this.attr.n); gl.vertexAttribPointer(this.attr.n, 3, gl.FLOAT, false, 32, 12);
    gl.enableVertexAttribArray(this.attr.uv); gl.vertexAttribPointer(this.attr.uv, 2, gl.FLOAT, false, 32, 24);
    gl.uniformMatrix4fv(L.mvp, false, mvp); gl.uniformMatrix4fv(L.model, false, model);
    gl.uniform4fv(L.col, opts.col || [1, 1, 1, 1]); gl.uniform1f(L.useTex, opts.tex ? 1 : 0);
    gl.uniform3fv(L.emis, opts.emis || [0, 0, 0]);
    gl.uniform1f(L.op, opts.op ?? 1);
    if (opts.tex) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, opts.tex); gl.uniform1i(L.tex, 0); }
    gl.drawArrays(gl.TRIANGLES, 0, buf.n);
  }

  // ------------------------------------------------------------ camera
  _fitDist() {
    const B = this.scene.board, t = Math.tan(0.31), aspect = (this.cw || 16) / (this.ch || 9), o = this.scene.box?.outer;
    const w = o ? Math.max(B.w, o[2] - o[0]) : B.w, h = o ? Math.max(B.h, o[3] - o[1]) : B.h;
    return Math.max(w / (2 * t * aspect), h * 1.3 / (2 * t)) * 1.15 + (o ? 10 : 6);
  }

  _eye() {
    const c = Math.cos(this.pitch);
    return [this.target[0] + this.dist * c * Math.sin(this.yaw), this.target[1] + this.dist * Math.sin(this.pitch),
            this.target[2] + this.dist * c * Math.cos(this.yaw)];
  }

  _matrices() {
    const aspect = this.cw / Math.max(1, this.ch);
    this.P = M4.persp(0.62, aspect, Math.max(0.5, this.dist / 100), this.dist * 6);
    this.V = M4.lookAt(this._eye(), this.target, [0, 1, 0]);
    this.VP = M4.mul(this.P, this.V);
  }

  project(x, y, z) {
    const m = this.VP, w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (w <= 0) return null;
    return [((m[0] * x + m[4] * y + m[8] * z + m[12]) / w * 0.5 + 0.5) * this.cw,
            (1 - ((m[1] * x + m[5] * y + m[9] * z + m[13]) / w * 0.5 + 0.5)) * this.ch, w];
  }

  // animate the camera to a view; resolves when it gets there
  flyTo(goal, ms = 900) {
    const from = { yaw: this.yaw, pitch: this.pitch, dist: this.dist, target: [...this.target] };
    const to = { yaw: goal.yaw ?? from.yaw, pitch: goal.pitch ?? from.pitch, dist: goal.dist ?? from.dist, target: goal.target ?? from.target };
    return new Promise(res => { this.anim = { from, to, t0: performance.now(), ms, res }; });
  }

  orbit(ms, turn) {
    const from = { yaw: this.yaw, pitch: this.pitch, dist: this.dist, target: [...this.target] };
    return this.flyTo({ yaw: from.yaw + turn }, ms).then(() => from);
  }

  home(ms = 900) { const B = this.scene.board; return this.flyTo({ yaw: -0.35, pitch: 0.95, dist: this.fitDist, target: [B.w / 2, 0, B.h / 2] }, ms); }

  focus(refs, ms = 900) {
    const ps = refs.map(r => this.parts[r]).filter(Boolean);
    if (!ps.length) return this.home(ms);
    const x0 = Math.min(...ps.map(p => p.box[0])), x1 = Math.max(...ps.map(p => p.box[2]));
    const z0 = Math.min(...ps.map(p => p.box[1])), z1 = Math.max(...ps.map(p => p.box[3]));
    const size = Math.max(x1 - x0, z1 - z0, 4);
    return this.flyTo({ target: [(x0 + x1) / 2, 0, (z0 + z1) / 2], dist: Math.min(this.fitDist, size * 2.4 + 30), pitch: 0.85 }, ms);
  }

  _anim(now) {
    const a = this.anim;
    if (!a) return;
    let t = Math.min(1, (now - a.t0) / a.ms);
    const e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
    this.yaw = a.from.yaw + (a.to.yaw - a.from.yaw) * e;
    this.pitch = a.from.pitch + (a.to.pitch - a.from.pitch) * e;
    this.dist = a.from.dist + (a.to.dist - a.from.dist) * e;
    this.target = a.from.target.map((v, i) => v + (a.to.target[i] - v) * e);
    if (t >= 1) { this.anim = null; a.res(); }
  }

  // ------------------------------------------------------------ input
  _bind() {
    let drag = null, moved = 0;
    const c = this.over;
    c.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY, e.button]; moved = 0; c.setPointerCapture(e.pointerId); this.anim = null; });
    c.addEventListener("pointermove", e => {
      if (!drag) { this.hover = this._pick(e); return; }
      const dx = e.clientX - drag[0], dy = e.clientY - drag[1];
      moved += Math.abs(dx) + Math.abs(dy);
      if (drag[2] === 2 || e.shiftKey) {        // pan
        const s = this.dist / this.H * 1.2, cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
        this.target[0] -= (dx * cy - dy * sy) * s; this.target[2] -= (-dx * sy - dy * cy) * s;
      } else {
        this.yaw -= dx * 0.008;
        this.pitch = Math.max(-1.45, Math.min(1.5, this.pitch + dy * 0.008));
      }
      drag[0] = e.clientX; drag[1] = e.clientY;
    });
    c.addEventListener("pointerup", e => { if (drag && moved < 5) { const r = this._pick(e); this.select(r, true); } drag = null; });
    c.addEventListener("contextmenu", e => e.preventDefault());
    c.addEventListener("wheel", e => { e.preventDefault(); this.anim = null; this.dist = Math.max(4, Math.min(this.fitDist * 3, this.dist * Math.exp(e.deltaY * 0.0012))); }, { passive: false });
  }

  select(ref, user) {
    this.sel = ref;
    if (user && this.opts.onSelect) this.opts.onSelect(ref);
  }

  _pick(e) {
    const r = this.over.getBoundingClientRect();
    const nx = (e.clientX - r.left) / r.width * 2 - 1, ny = 1 - (e.clientY - r.top) / r.height * 2;
    const inv = M4.invert(this.VP);
    const un = (x, y, z) => { const w = inv[3] * x + inv[7] * y + inv[11] * z + inv[15];
      return [0, 1, 2].map(i => (inv[i] * x + inv[4 + i] * y + inv[8 + i] * z + inv[12 + i]) / w); };
    const a = un(nx, ny, -1), b = un(nx, ny, 1), d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    let best = null, bt = Infinity;
    for (const p of this.scene.parts) {
      const [y0, y1] = this._ySpan(p);
      let t0 = -Infinity, t1 = Infinity, ok = true;
      for (const [o, dd, lo, hi] of [[a[0], d[0], p.box[0], p.box[2]], [a[1], d[1], y0, y1], [a[2], d[2], p.box[1], p.box[3]]]) {
        if (Math.abs(dd) < 1e-9) { if (o < lo || o > hi) { ok = false; break; } continue; }
        let ta = (lo - o) / dd, tb = (hi - o) / dd;
        if (ta > tb) [ta, tb] = [tb, ta];
        t0 = Math.max(t0, ta); t1 = Math.min(t1, tb);
      }
      if (ok && t0 <= t1 && t0 > 0 && t0 < bt) { bt = t0; best = p.ref; }
    }
    return best;
  }

  _ySpan(p) { const t = this.scene.board.t, h = Math.max(0.3, p.h); return p.side === "bottom" ? [-t - h, -t] : [0, h]; }

  _resize() {
    const r = this.host.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
    this.W = Math.max(50, r.width); this.H = Math.max(50, r.height);
    const [w, h] = this.forced || [this.W * dpr, this.H * dpr];
    for (const c of [this.gl3, this.over]) { c.width = w; c.height = h; }
    this.cw = w; this.ch = h; this.scale = this.forced ? 1.3 * h / 720 : dpr;
    const fit = this._fitDist();
    if (!this.fitDist || Math.abs(this.dist - this.fitDist) < 1e-6) this.dist = fit;     // keep a fitted view fitted
    this.fitDist = fit;
  }

  // ------------------------------------------------------------ drawing
  _colour(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  _drawTexture(now) {
    const s = this.scene, c = this.tctx, k = this.ppm, W = this.tex.width, H = this.tex.height;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, W, H);
    c.save();
    c.scale(k, k);
    c.beginPath(); s.outline.forEach((p, i) => i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])); c.closePath(); c.clip();
    c.fillStyle = this.maskCol || "#1f4a35"; c.fillRect(0, 0, s.board.w, s.board.h);
    const poly = (pts) => { c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])); c.closePath(); };
    // copper under the mask shows as a lighter green
    c.fillStyle = "rgba(120,190,120,0.16)";
    for (const z of s.zones) if (z.layer === "F.Cu") { poly(z.pts); c.fill(); }
    c.lineCap = "round";
    for (const t of s.tracks) if (t.layer === "F.Cu") { c.strokeStyle = "rgba(150,220,140,0.35)"; c.lineWidth = t.w; c.beginPath(); c.moveTo(...t.a); c.lineTo(...t.b); c.stroke(); }
    for (const t of s.tracks) if (t.layer !== "F.Cu") { c.strokeStyle = "rgba(150,220,140,0.10)"; c.lineWidth = t.w; c.beginPath(); c.moveTo(...t.a); c.lineTo(...t.b); c.stroke(); }
    if (this.show.heat) this._drawHeat(c);
    if (this.show.flow) this._drawFlow(c, now);
    for (const p of s.pads) if (p.front) {
      c.fillStyle = "#d8b25a";
      c.save(); c.translate(p.x, p.y); c.rotate(-p.rot * Math.PI / 180);
      const r = p.shape === "circle" || p.shape === "oval" ? Math.min(p.w, p.h) / 2 : p.shape === "roundrect" ? Math.min(p.w, p.h) * 0.25 : 0;
      c.beginPath(); c.roundRect ? c.roundRect(-p.w / 2, -p.h / 2, p.w, p.h, r) : c.rect(-p.w / 2, -p.h / 2, p.w, p.h); c.fill(); c.restore();
    }
    for (const v of s.vias) { c.fillStyle = "#c9a24e"; c.beginPath(); c.arc(v.p[0], v.p[1], v.d / 2, 0, 7); c.fill(); }
    c.globalCompositeOperation = "destination-out";
    for (const h of s.holes) { c.beginPath(); c.arc(h.x, h.y, h.d / 2, 0, 7); c.fill(); }
    for (const p of s.pads) if (p.drill) { c.beginPath(); c.arc(p.x, p.y, p.drill / 2, 0, 7); c.fill(); }
    for (const v of s.vias) { c.beginPath(); c.arc(v.p[0], v.p[1], v.d / 4, 0, 7); c.fill(); }
    c.restore();
  }

  _drawHeat(c) {
    const T = this.thermal, img = this.heatImg;
    if (img.width !== T.nx) { img.width = T.nx; img.height = T.ny; }
    const ictx = img.getContext("2d"), data = ictx.createImageData(T.nx, T.ny);
    // the board keeps its own range; the box's walls, cooler than any of it, show at the cool end
    const lo = T.minTemp() - T.settings.ambient;
    const span = Math.max(1, T.maxTemp() - T.settings.ambient - lo);
    this.heatLo = lo; this.heatSpan = span;
    for (let i = 0; i < T.theta.length; i++) {
      if (!T.den[i]) continue;
      const [r, g, b] = slHeat((T.theta[i] - lo) / span);
      data.data.set([r, g, b, 235], i * 4);
    }
    ictx.putImageData(data, 0, 0);
    c.imageSmoothingEnabled = true;
    c.drawImage(img, 0, 0, T.nx * T.dx, T.ny * T.dx);
  }

  _drawFlow(c, now) {
    const s = this.scene, t = (now - this.t0) / 1000;
    for (const tr of s.tracks) {
      const ma = (this.dyn.net_ma || {})[tr.net] || 0;
      if (ma < 0.05) continue;
      const kind = s.net_kind[tr.net] || "signal", col = SL_FLOW[kind];
      const f = Math.min(1, Math.log10(1 + ma) / Math.log10(1 + this.maxMa));
      const [a, b] = tr.dir < 0 ? [tr.b, tr.a] : [tr.a, tr.b];
      const back = tr.layer !== "F.Cu";
      c.globalAlpha = (back ? 0.35 : 1) * (0.25 + 0.35 * f);
      c.strokeStyle = col; c.lineWidth = tr.w + 0.5;
      c.setLineDash([]); c.beginPath(); c.moveTo(...a); c.lineTo(...b); c.stroke();
      c.globalAlpha = back ? 0.45 : 1;
      c.lineWidth = Math.max(0.12, tr.w * 0.6);
      c.setLineDash([0.9, 1.4]);
      c.lineDashOffset = -t * (2 + 9 * f);
      c.beginPath(); c.moveTo(...a); c.lineTo(...b); c.stroke();
    }
    c.setLineDash([]); c.globalAlpha = 1;
  }

  _partColour(p) {
    const st = this.dyn.state[p.ref] || {}, fp = (p.footprint || "").toLowerCase(), ref = p.ref;
    if (this.show.heat) {
      const T = this.thermal.partTemp(p.ref);
      if (T != null) { const c = slHeat((T - this.thermal.settings.ambient - (this.heatLo || 0)) / (this.heatSpan || 10)); return [c[0] / 255, c[1] / 255, c[2] / 255, 1]; }
    }
    if (st.light) return [0.93, 0.92, 0.88, 1];
    if (/^(U|IC)\d/.test(ref)) return /module|esp32|wroom/.test(fp) ? [0.72, 0.74, 0.77, 1] : [0.12, 0.12, 0.14, 1];
    if (/^R\d/.test(ref)) return [0.1, 0.1, 0.1, 1];
    if (/^C\d/.test(ref)) return /cp_|elec|tantal/.test(fp) ? [0.75, 0.62, 0.25, 1] : [0.66, 0.52, 0.36, 1];
    if (/^(J|P|CN)\d/.test(ref)) return /usb/.test(fp) ? [0.75, 0.77, 0.8, 1] : [0.9, 0.87, 0.78, 1];
    if (/^(SW|S)\d/.test(ref)) return [0.3, 0.3, 0.32, 1];
    if (/^(Y|X)\d/.test(ref)) return [0.78, 0.8, 0.82, 1];
    if (/^L\d/.test(ref)) return [0.25, 0.25, 0.27, 1];
    if (/^(JP|TP)\d/.test(ref)) return [0.85, 0.7, 0.35, 1];
    return [0.2, 0.2, 0.22, 1];
  }

  _light(ref) {
    // the mixed colour and brightness (0..1) of an LED
    const ch = this.dyn.state[ref]?.light;
    if (!ch?.length) return null;
    let r = 0, g = 0, b = 0, tot = 0;
    for (const c of ch) { const k = Math.sqrt(Math.max(0, Math.min(1.6, c.ma / (c.max_ma || 20)))); r += c.color[0] * k; g += c.color[1] * k; b += c.color[2] * k; tot += k; }
    if (tot < 0.02) return null;
    const m = Math.max(r, g, b) || 1;
    return { rgb: [r / m * 255, g / m * 255, b / m * 255], k: Math.min(1.3, tot / Math.sqrt(ch.length)), over: ch.some(c => c.ma > c.max_ma) };
  }

  _frame() {
    const now = performance.now(), gl = this.gl;
    if (!this.cw) return;
    this._anim(now);
    if (this.spin && !this.anim) this.yaw += this.spin;
    // keep solving in the background so part temperatures in labels are right even with the map off
    const heatMoved = this.thermal.step(this.show.heat ? 12 : 3) && this.show.heat;
    const shown = JSON.stringify(this.show);
    if (shown !== this._shown) { this._shown = shown; this.texDirty = true; }
    if (this.show.flow || heatMoved || this.texDirty) {
      if (this.texDirty) { this.maskCol = this._colour("--board-mask"); this._botDirty = true; }
      this._drawTexture(now); this._texUpload(this.glTex, this.tex); this.texDirty = false;
      if (this._botDirty) {
        const b = document.createElement("canvas"); b.width = this.tex.width; b.height = this.tex.height;
        const bc = b.getContext("2d"); bc.scale(this.ppm, this.ppm);
        bc.beginPath(); this.scene.outline.forEach((p, i) => i ? bc.lineTo(p[0], p[1]) : bc.moveTo(p[0], p[1])); bc.closePath();
        bc.fillStyle = this.maskCol || "#1f4a35"; bc.fill();
        this._texUpload(this.glBotTex, b); this._botDirty = false;
      }
    }
    this._matrices();
    gl.viewport(0, 0, this.cw, this.ch);
    const bg = this._colour("--board-bg") || "#0f1512";
    const rgb = bg.startsWith("#") ? [1, 3, 5].map(i => parseInt(bg.length === 4 ? bg[(i + 1) / 2].repeat(2) : bg.slice(i, i + 2), 16) / 255) : [0.06, 0.08, 0.07];
    gl.clearColor(...rgb, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const e = this._eye(), d = [e[0] - this.target[0] - 20, e[1] + 40, e[2] - this.target[2] + 10], l = Math.hypot(...d);
    gl.uniform3fv(this.loc.L, d.map(x => x / l));
    const I = M4.box(0, 0, 0, 1, 1, 1);
    this._draw(this.top, this.VP, I, { tex: this.glTex });
    this._draw(this.bot, this.VP, I, { tex: this.glBotTex });
    this._draw(this.wall, this.VP, I, { col: [0.55, 0.6, 0.45, 1] });
    for (const p of this.scene.parts) {
      const [y0, y1] = this._ySpan(p);
      const M = M4.box(p.box[0], y0, p.box[1], p.box[2], y1, p.box[3]);
      const lit = this.show.glow && !this.show.heat ? this._light(p.ref) : null;
      let col = this._partColour(p), emis = [0, 0, 0];
      if (lit) { emis = lit.rgb.map(v => v / 255 * 0.55 * lit.k); col = [0.5 + lit.rgb[0] / 512, 0.5 + lit.rgb[1] / 512, 0.5 + lit.rgb[2] / 512, 1]; }
      const hl = p.ref === this.sel || this.focusRefs.includes(p.ref) || p.ref === this.hover;
      if (hl) { const pulse = 0.18 + 0.12 * Math.sin(now / 160); emis = emis.map((v, i) => v + pulse * [1, 0.85, 0.3][i]); }
      this._draw(this.cube, M4.mul(this.VP, M), M, { col, emis });
    }
    if (this.show.box && this.scene.box && this.dyn.box_heat) this._drawBox(e);
    this._overlay(now);
    if (this.rec && this.recCtx) this.recCtx.drawImage(this.gl3, 0, 0), this.recCtx.drawImage(this.over, 0, 0);
  }

  // the enclosure, see-through: each wall a slab coloured by its outside temperature, vents as dark slots
  _drawBox(eye) {
    const gl = this.gl, b = this.scene.box, bh = this.dyn.box_heat, [x0, y0, x1, y1] = b.outer;
    const z0 = b.z0, z1 = b.z1, w = b.wall;
    const heat = this.show.heat, amb = this.thermal.settings.ambient;
    const col = t => {
      if (!heat) return [0.78, 0.82, 0.86, 1];
      const c = slHeat((t - amb - (this.heatLo || 0)) / (this.heatSpan || 10));
      return [c[0] / 255, c[1] / 255, c[2] / 255, 1];
    };
    const S = bh.surface_c;
    const slabs = [
      { box: [x0, z1 - b.lid, y0, x1, z1, y1], t: S.lid, op: 0.16 },
      { box: [x0, z0, y0, x1, z0 + b.floor, y1], t: S.base, op: 0.5 },
      { box: [x0, z0, y0, x0 + w, z1 - b.lid, y1], t: S.sides, op: 0.24 },
      { box: [x1 - w, z0, y0, x1, z1 - b.lid, y1], t: S.sides, op: 0.24 },
      { box: [x0 + w, z0, y0, x1 - w, z1 - b.lid, y0 + w], t: S.sides, op: 0.24 },
      { box: [x0 + w, z0, y1 - w, x1 - w, z1 - b.lid, y1], t: S.sides, op: 0.24 },
    ];
    const dark = [0.08, 0.09, 0.1, 1];
    for (const r of bh.lid_slots || []) slabs.push({ box: [r[0], z1 - b.lid - 0.05, r[1], r[2], z1 + 0.05, r[3]], col: dark, op: 0.5 });
    for (const sl of bh.side_slots || []) {
      const zz = [sl.z0, sl.z1];
      const bx = sl.wall === "left" ? [x0 - 0.05, zz[0], sl.a, x0 + w + 0.05, zz[1], sl.b]
        : sl.wall === "right" ? [x1 - w - 0.05, zz[0], sl.a, x1 + 0.05, zz[1], sl.b]
        : sl.wall === "top" ? [sl.a, zz[0], y0 - 0.05, sl.b, zz[1], y0 + w + 0.05]
        : [sl.a, zz[0], y1 - w - 0.05, sl.b, zz[1], y1 + 0.05];
      slabs.push({ box: bx, col: dark, op: 0.5 });
    }
    // far ones first, so the see-through walls blend in order
    const dist = s => Math.hypot((s.box[0] + s.box[3]) / 2 - eye[0], (s.box[1] + s.box[4]) / 2 - eye[1], (s.box[2] + s.box[5]) / 2 - eye[2]);
    slabs.sort((a, c) => dist(c) - dist(a));
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
    for (const s of slabs) {
      const M = M4.box(...s.box);
      this._draw(this.cube, M4.mul(this.VP, M), M, { col: s.col || col(s.t), op: s.op });
    }
    gl.depthMask(true); gl.disable(gl.BLEND);
  }

  _overlay(now) {
    const c = this.octx, k = this.scale || 1;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, this.cw, this.ch);
    // LED glow
    if (this.show.glow && !this.show.heat) {
      c.globalCompositeOperation = "lighter";
      for (const p of this.scene.parts) {
        const lit = this._light(p.ref);
        if (!lit) continue;
        const [, y1] = this._ySpan(p);
        const pt = this.project((p.box[0] + p.box[2]) / 2, y1 + 0.2, (p.box[1] + p.box[3]) / 2);
        if (!pt) continue;
        const r = Math.max(16, 4200 / pt[2]) * k * (0.7 + 0.6 * lit.k);
        const g = c.createRadialGradient(pt[0], pt[1], 0, pt[0], pt[1], r);
        const [R, G, B] = lit.rgb.map(Math.round);
        g.addColorStop(0, `rgba(255,255,255,${Math.min(1, 0.85 * lit.k)})`); g.addColorStop(0.12, `rgba(${R},${G},${B},${Math.min(1, 0.8 * lit.k)})`);
        g.addColorStop(0.4, `rgba(${R},${G},${B},${0.25 * lit.k})`); g.addColorStop(1, `rgba(${R},${G},${B},0)`);
        c.fillStyle = g; c.beginPath(); c.arc(pt[0], pt[1], r, 0, 7); c.fill();
      }
      c.globalCompositeOperation = "source-over";
    }
    // markers on the parts each risk mentions
    const risks = this.dyn.risks || [];
    for (const rk of risks) for (const ref of rk.refs) {
      const p = this.parts[ref];
      if (!p || (this.focusRefs.length && !this.focusRefs.includes(ref))) continue;
      const [, y1] = this._ySpan(p);
      const pt = this.project((p.box[0] + p.box[2]) / 2, y1 + 0.3, (p.box[1] + p.box[3]) / 2);
      if (!pt) continue;
      const pulse = (now / 1200) % 1, col = SL_LEVEL[rk.level];
      c.strokeStyle = col; c.globalAlpha = 1 - pulse; c.lineWidth = 2 * k;
      c.beginPath(); c.arc(pt[0], pt[1], (8 + 22 * pulse) * k, 0, 7); c.stroke();
      c.globalAlpha = 1; c.fillStyle = col; c.beginPath(); c.arc(pt[0], pt[1], 3.5 * k, 0, 7); c.fill();
    }
    // labels
    const label = (ref, strong) => {
      const p = this.parts[ref];
      if (!p) return;
      const [, y1] = this._ySpan(p);
      const pt = this.project((p.box[0] + p.box[2]) / 2, y1, (p.box[1] + p.box[3]) / 2);
      if (!pt) return;
      const lines = strong ? this.describe(ref) : [ref];
      c.font = `${strong ? 600 : 500} ${12 * k}px Inter, system-ui, sans-serif`;
      const w = Math.max(...lines.map(l => c.measureText(l).width)) + 14 * k, h = (lines.length * 16 + 8) * k;
      const x = Math.min(this.cw - w - 4, Math.max(4, pt[0] + 12 * k)), y = Math.max(4, pt[1] - h - 10 * k);
      c.fillStyle = "rgba(8,12,14,0.82)"; c.strokeStyle = "rgba(255,255,255,0.25)"; c.lineWidth = 1;
      c.beginPath(); c.roundRect ? c.roundRect(x, y, w, h, 6 * k) : c.rect(x, y, w, h); c.fill(); c.stroke();
      c.strokeStyle = "rgba(255,255,255,0.5)"; c.beginPath(); c.moveTo(pt[0], pt[1]); c.lineTo(x, y + h); c.stroke();
      c.fillStyle = "#fff";
      lines.forEach((l, i) => { c.font = `${i ? 400 : 600} ${12 * k}px Inter, system-ui, sans-serif`; c.fillText(l, x + 7 * k, y + (16 * i + 16) * k); });
    };
    if (this.show.labels) for (const p of this.scene.parts) if (p.ref !== this.sel && p.ref !== this.hover) label(p.ref, false);
    for (const r of new Set([...this.focusRefs.slice(0, 3), this.hover, this.sel].filter(Boolean))) label(r, true);
    // legend
    c.font = `500 ${11 * k}px Inter, system-ui, sans-serif`;
    const lx = 12 * k, ly = 12 * k;
    if (this.show.heat) {
      const amb = this.thermal.settings.ambient + (this.heatLo || 0), span = this.heatSpan || 10, w = 160 * k;
      const bx = this.dyn.box_heat, inBox = bx?.on;
      c.fillStyle = "rgba(8,12,14,0.75)"; c.fillRect(lx - 6 * k, ly - 4 * k, Math.max(w, inBox ? 300 * k : 0) + 12 * k, 40 * k);
      for (let i = 0; i < w; i++) { const [r, g, b] = slHeat(i / w); c.fillStyle = `rgb(${r},${g},${b})`; c.fillRect(lx + i, ly + 16 * k, 1, 10 * k); }
      c.fillStyle = "#fff";
      c.fillText((inBox ? `In the box: ${bx.room_c.toFixed(0)} °C room, ${bx.air_c.toFixed(1)} °C inside` : `Board temperature in ${this.thermal.settings.ambient.toFixed(0)} °C air`)
        + (this.thermal.converged ? "" : " (settling…)"), lx, ly + 10 * k);
      c.fillText(`${amb.toFixed(1)} °C`, lx, ly + 36 * k + 2);
      const hi = `${(amb + span).toFixed(1)} °C`; c.fillText(hi, lx + w - c.measureText(hi).width, ly + 36 * k + 2);
    } else if (this.show.flow) {
      c.fillStyle = "rgba(8,12,14,0.75)"; c.fillRect(lx - 6 * k, ly - 4 * k, 250 * k, 22 * k);
      let x = lx;
      for (const [kind, col] of Object.entries(SL_FLOW)) {
        c.fillStyle = col; c.fillRect(x, ly + 4 * k, 14 * k, 4 * k); x += 18 * k;
        c.fillStyle = "#fff"; const t = { power: "supply current", ground: "return", signal: "signals" }[kind]; c.fillText(t, x, ly + 10 * k); x += c.measureText(t).width + 12 * k;
      }
    }
    const bx = this.dyn.box_heat;
    if (bx?.on && this.show.box && this.scene.box) {
      // the box's edges, so its shape reads through the see-through walls
      const [x0, y0, x1, y1] = this.scene.box.outer, z0 = this.scene.box.z0, z1 = this.scene.box.z1;
      const P = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
      c.strokeStyle = "rgba(220,230,240,0.45)"; c.lineWidth = 1.2 * k;
      c.beginPath();
      for (let i = 0; i < 4; i++) {
        const [a, b] = [P[i], P[(i + 1) % 4]];
        for (const [p0, p1] of [[[a[0], z0, a[1]], [b[0], z0, b[1]]], [[a[0], z1, a[1]], [b[0], z1, b[1]]], [[a[0], z0, a[1]], [a[0], z1, a[1]]]]) {
          const A = this.project(...p0), B = this.project(...p1);
          if (A && B) { c.moveTo(A[0], A[1]); c.lineTo(B[0], B[1]); }
        }
      }
      c.stroke();
    }
    if (bx?.on && this.show.box) {
      const lines = [`Inside air ${bx.air_c.toFixed(1)} °C`, `Lid ${bx.surface_c.lid.toFixed(1)} · walls ${bx.surface_c.sides.toFixed(1)} · base ${bx.surface_c.base.toFixed(1)} °C`,
        `${bx.vents_label}, ${bx.material}` + (bx.flow_l_min >= 0.05 ? ` · ${bx.flow_l_min.toFixed(1)} l/min of air` : "")];
      c.font = `500 ${11 * k}px Inter, system-ui, sans-serif`;
      const bw = Math.max(...lines.map(l => c.measureText(l).width)) + 14 * k, x = this.cw - bw - 12 * k;
      c.fillStyle = "rgba(8,12,14,0.75)"; c.fillRect(x, 8 * k, bw, (lines.length * 15 + 8) * k);
      c.fillStyle = "#fff"; lines.forEach((l, i) => c.fillText(l, x + 7 * k, (22 + 15 * i) * k));
    }
    if (this.caption) this._caption(c, k);
  }

  describe(ref) {
    const p = this.parts[ref], st = this.dyn.state[ref] || {}, out = [`${ref}  ${p?.value || ""}`.trim()];
    const bits = [];
    if (st.current_ma != null && !st.light) bits.push(`${st.current_ma.toFixed(st.current_ma < 10 ? 2 : 1)} mA`);
    if (st.power_mw) bits.push(`${st.power_mw.toFixed(st.power_mw < 10 ? 1 : 0)} mW`);
    const T = this.thermal.partTemp(ref);
    if (T != null && st.power_mw) bits.push(`${T.toFixed(1)} °C`);
    if (bits.length) out.push(bits.join(" · "));
    if (st.light) out.push(st.light.map(c => `${c.ma.toFixed(1)} mA`).join(" / ") + (st.light.length > 1 ? " per colour" : ""));
    const desc = this.scene.descriptions?.[ref];
    if (desc) out.push(desc.length > 42 ? desc.slice(0, 40) + "…" : desc);
    return out;
  }

  _caption(c, k) {
    const cap = this.caption, pad = 16 * k, x = 24 * k, maxW = Math.min(this.cw - 48 * k, 760 * k);
    c.font = `600 ${22 * k}px Inter, system-ui, sans-serif`;
    const words = (cap.text || "").split(" "), lines = [];
    c.font = `400 ${15 * k}px Inter, system-ui, sans-serif`;
    let line = "";
    for (const w of words) { const t = line ? line + " " + w : w; if (c.measureText(t).width > maxW - 2 * pad && line) { lines.push(line); line = w; } else line = t; }
    if (line) lines.push(line);
    const h = pad * 2 + (cap.kicker ? 18 * k : 0) + 28 * k + lines.length * 21 * k;
    const y = this.ch - h - 24 * k;
    c.fillStyle = "rgba(6,10,12,0.86)"; c.beginPath(); c.roundRect ? c.roundRect(x, y, maxW, h, 10 * k) : c.rect(x, y, maxW, h); c.fill();
    c.fillStyle = SL_LEVEL[cap.level] || "#37b37b"; c.fillRect(x, y, 4 * k, h);
    let ty = y + pad;
    if (cap.kicker) { c.font = `700 ${11 * k}px Inter, system-ui, sans-serif`; c.fillStyle = SL_LEVEL[cap.level] || "#37b37b"; c.fillText(cap.kicker.toUpperCase(), x + pad, ty + 11 * k); ty += 18 * k; }
    c.font = `650 ${22 * k}px Inter, system-ui, sans-serif`; c.fillStyle = "#fff"; c.fillText(cap.title, x + pad, ty + 21 * k); ty += 28 * k;
    c.font = `400 ${15 * k}px Inter, system-ui, sans-serif`; c.fillStyle = "rgba(255,255,255,0.82)";
    lines.forEach((l, i) => c.fillText(l, x + pad, ty + 16 * k + i * 21 * k));
    const prog = cap.progress ?? this.tourProgress;
    if (prog != null) { c.fillStyle = "rgba(255,255,255,0.15)"; c.fillRect(x, y + h - 3 * k, maxW, 3 * k); c.fillStyle = SL_LEVEL[cap.level] || "#37b37b"; c.fillRect(x, y + h - 3 * k, maxW * prog, 3 * k); }
  }

  // ------------------------------------------------------------ recording
  startRecording() {
    const types = ["video/mp4;codecs=avc1", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
    const type = types.find(t => window.MediaRecorder?.isTypeSupported?.(t));
    if (!type) throw new Error("this browser cannot record video");
    this.forced = [1280, 720];
    this.wrap.classList.add("recording");
    this._resize();
    const rc = document.createElement("canvas"); rc.width = 1280; rc.height = 720;
    this.recCtx = rc.getContext("2d");
    const chunks = [];
    this.rec = new MediaRecorder(rc.captureStream(30), { mimeType: type, videoBitsPerSecond: 6e6 });
    this.rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    this.recDone = new Promise(res => { this.rec.onstop = () => res(new Blob(chunks, { type: type.split(";")[0] })); });
    this.rec.start(500);
  }

  async stopRecording() {
    if (!this.rec) return null;
    this.rec.stop();
    const blob = await this.recDone;
    this.rec = null; this.recCtx = null; this.forced = null;
    this.wrap.classList.remove("recording");
    this._resize();
    return blob;
  }
}

// ---------------------------------------------------------------- the guided tour (and the video)
const slWait = ms => new Promise(r => setTimeout(r, ms));

async function slTour(lab, opts = {}) {
  // opts.whatif(values) → new numbers from a re-simulation; opts.stopped() → true to end early
  const sc = lab.scene, dyn0 = lab.dyn, stop = () => opts.stopped?.();
  const sup = (dyn0.supplies || []).map(s => `${s.net} supplies ${s.current_ma.toFixed(0)} mA at ${s.volts} V`).join(", ");
  const steps = [];
  const leds = sc.parts.filter(p => dyn0.state[p.ref]?.light).map(p => p.ref);
  const hot = Object.entries(dyn0.state).filter(([, s]) => s.power_mw).sort((a, b) => b[1].power_mw - a[1].power_mw);
  steps.push(async () => {
    lab.show = { flow: false, heat: false, glow: true, labels: false };
    lab.caption = { kicker: "WholeBoard Sim Lab", title: sc.name || "Your board", text: `The ngspice simulation of this board, drawn on the routed layout: ${sc.parts.length} parts on a ${sc.board.w.toFixed(0)} × ${sc.board.h.toFixed(0)} mm, ${sc.board.layers}-layer board.` };
    await lab.home(1200); await lab.orbit(3200, 0.7);
  });
  steps.push(async () => {
    lab.show.flow = true;
    lab.caption = { kicker: "Current flow", title: "Where the current goes", text: (sup ? sup + ". " : "") + "Dashes run along each track from the supply to the parts and back on the return; brighter and faster means more current." };
    await lab.flyTo({ pitch: 1.2, dist: lab.fitDist * 0.85 }, 1500); await lab.orbit(3000, -0.5);
  });
  if (leds.length) steps.push(async () => {
    lab.focusRefs = leds;
    const st = dyn0.state[leds[0]];
    lab.caption = { kicker: "LEDs", title: `${leds.length} LED${leds.length > 1 ? "s" : ""} lit by the simulation`, text: `Each glows as bright as its simulated current: ${leds[0]} gets ${st.light.map(c => c.ma.toFixed(1) + " mA").join(" / ")}.` };
    await lab.focus(leds, 1400); await slWait(2600); lab.focusRefs = [];
  });
  steps.push(async () => {
    lab.show.flow = false; lab.show.heat = true; lab.thermal.dirty = true; lab.thermal.warm = false;
    lab.caption = { kicker: "Heat", title: "How warm the board runs", text: hot.length ? `${hot[0][0]} gives off the most heat (${hot[0][1].power_mw.toFixed(0)} mW). Copper spreads it and both faces lose it to the air (still air, 1 oz copper).` : "Nothing on this board gives off noticeable heat." };
    await lab.home(1200); await lab.orbit(3500, 0.6);
    const T = lab.thermal.maxTemp();
    lab.caption = { ...lab.caption, text: `The hottest spot settles at ${T.toFixed(1)} °C in ${lab.thermal.settings.ambient.toFixed(0)} °C air` + (hot.length ? `, around ${hot[0][0]}.` : ".") };
    if (hot.length) { lab.focusRefs = [hot[0][0]]; await lab.focus([hot[0][0]], 1400); await slWait(1800); lab.focusRefs = []; }
  });
  if (sc.box && dyn0.box_heat && opts.whatif) steps.push(async () => {
    const bx0 = dyn0.box_heat;
    lab.show = { flow: false, heat: true, glow: false, labels: false, box: true };
    let d = dyn0;
    if (!bx0.on) { d = await opts.whatif({ box: 1 }); lab.update(d); lab.show.box = true; lab.show.heat = true; }
    const bx = d.box_heat;
    lab.caption = { kicker: "In its enclosure", title: `Inside ${sc.box.name}`, text: `${(bx.power_w * 1000).toFixed(0)} mW warms the air inside to ${bx.air_c.toFixed(1)} °C, ${bx.rise_k.toFixed(0)} °C above the ${bx.room_c.toFixed(0)} °C room. The lid sits at ${bx.surface_c.lid.toFixed(0)} °C.` };
    await lab.flyTo({ pitch: 0.55, dist: lab.fitDist * 1.05, target: [sc.board.w / 2, 0, sc.board.h / 2] }, 1500); await lab.orbit(3200, 0.6);
    if (bx.vents !== "both") {
      try {
        const v = await opts.whatif({ box: 1, vents: "both" });
        lab.update(v); lab.show.box = true; lab.show.heat = true;
        lab.caption = { ...lab.caption, title: "With lid and side vents", text: `Cool air in low down, warm air out through the lid: the inside drops to ${v.box_heat.air_c.toFixed(1)} °C (${(bx.air_c - v.box_heat.air_c).toFixed(1)} °C cooler), with ${v.box_heat.flow_l_min.toFixed(1)} litres of air a minute.` };
        await lab.orbit(3200, -0.5);
      } finally { lab.update(dyn0); }
    }
  });
  const risks = (dyn0.risks || []).slice(0, 4);
  risks.forEach((rk, i) => steps.push(async () => {
    lab.show.heat = /Heat/.test(rk.source) || /°C|hot|heat/.test(rk.text);
    lab.show.flow = !lab.show.heat;
    lab.focusRefs = rk.refs;
    lab.caption = { kicker: `${rk.level === "watch" ? "Worth watching" : rk.level} · ${rk.source} · ${i + 1} of ${risks.length}`, title: rk.refs.length ? rk.refs.slice(0, 4).join(", ") : rk.nets.join(", ") || "The board", text: rk.text, level: rk.level };
    await (rk.refs.length ? lab.focus(rk.refs, 1300) : lab.home(1300)); await slWait(3000);
    lab.focusRefs = [];
  }));
  // what-if: change one resistor and show the difference
  const led = leds.find(l => (dyn0.links?.[l] || []).some(id => id.startsWith("r:")));
  const rp = led && dyn0.params.find(p => p.id === dyn0.links[led].find(id => id.startsWith("r:")));
  if (rp && opts.whatif) steps.push(async () => {
    const lower = rp.value / 2.2;
    lab.show = { flow: true, heat: false, glow: true, labels: false };
    lab.caption = { kicker: "What if", title: `${rp.ref} at ${slOhms(lower)} instead of ${slOhms(rp.value)}?`, text: "Sliders re-run ngspice in under a second. Watch the brightness, the current and the heat change." };
    const focus = [rp.ref, led];
    lab.focusRefs = focus;
    await lab.focus(focus, 1200);
    try {
      const d = await opts.whatif({ [rp.id]: lower });
      await slWait(600); lab.update(d);
      const st = d.state[led];
      if (st?.light) lab.caption = { ...lab.caption, text: `${led} now draws ${st.light.map(c => c.ma.toFixed(1) + " mA").join(" / ")}. ` + ((d.risks || []).filter(r => r.level === "problem").map(r => r.text)[0] || "") };
      await slWait(2800);
    } finally { lab.update(dyn0); lab.focusRefs = []; }
  });
  steps.push(async () => {
    lab.show = { flow: true, heat: false, glow: true, labels: false };
    lab.caption = { kicker: "Try it yourself", title: "Click any risk or part", text: "Drag the sliders beside the board to change resistor values, supply voltages, load currents, the air temperature and cooling." };
    await lab.home(1300); await lab.orbit(2500, 0.5);
  });
  for (let i = 0; i < steps.length && !stop(); i++) {
    lab.tourProgress = (i + 1) / steps.length;
    await steps[i]();
  }
  lab.caption = null; lab.focusRefs = []; lab.tourProgress = null;
}

function slOhms(v) { return v >= 1e6 ? `${+(v / 1e6).toPrecision(3)} MΩ` : v >= 1e3 ? `${+(v / 1e3).toPrecision(3)} kΩ` : `${+v.toPrecision(3)} Ω`; }
