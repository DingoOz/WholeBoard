"""KiCad 3D models: find or download them so renders show real parts, and measure part heights.

Footprints point at models as ${KICADn_3DMODEL_DIR}/<Lib>.3dshapes/<Name>.step. KiCad's installers
usually bring the models; when they are missing (the Docker image, Linux packages without
kicad-packages3D) each model a board uses is downloaded once from KiCad's GitLab into a cache.
"""
from __future__ import annotations

import glob
import json
import math
import os
import platform
import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

CACHE = config.APP_DIR / "models3d"
RAW = "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/{tag}/{rel}"
TAGS = ("10.0.6", "master")
MAX_BYTES = 40_000_000
MODEL_RE = re.compile(r'\(model\s+"([^"]+)"')
_lock = threading.Lock()


def rel_path(path: str) -> str | None:
    """'${KICAD10_3DMODEL_DIR}/Package_SO.3dshapes/SOIC-8.wrl' -> 'Package_SO.3dshapes/SOIC-8.step'."""
    m = re.search(r"([^/\\]+\.3dshapes)[/\\]([^/\\]+)$", path)
    if not m:
        return None
    return f"{m.group(1)}/{Path(m.group(2)).stem}.step"


def _system_dirs() -> list[Path]:
    out = [Path(os.environ[e]) for e in ("KICAD10_3DMODEL_DIR", "KICAD9_3DMODEL_DIR", "KICAD8_3DMODEL_DIR")
           if os.environ.get(e)]
    extra = (config.load().get("tools") or {}).get("models3d_dir")
    if extra:
        out.append(Path(extra).expanduser())
    system = platform.system()
    if system == "Darwin":
        out += [Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels"),
                Path.home() / "Library/Application Support/kicad/3dmodels"]
    elif system == "Windows":
        out += [Path(p) for p in sorted(glob.glob(r"C:\Program Files\KiCad\*\share\kicad\3dmodels"), reverse=True)]
    else:
        out += [Path("/usr/share/kicad/3dmodels"), Path("/usr/local/share/kicad/3dmodels"),
                Path.home() / ".var/app/org.kicad.KiCad/data/kicad/3dmodels"]
    return [d for d in out if d.is_dir()]


def local(rel: str) -> Path | None:
    """A model already on this computer, in KiCad's folders or the download cache."""
    for d in _system_dirs() + [CACHE]:
        f = d / rel
        if f.is_file() and f.stat().st_size > 0:
            return f
    return None


def _missing() -> dict:
    try:
        return json.loads((CACHE / "missing.json").read_text())
    except (OSError, ValueError):
        return {}


def fetch(rel: str, timeout: float = 40) -> Path | None:
    """Download one model into the cache. Returns None if KiCad has no such model."""
    if rel in _missing():
        return None
    dest = CACHE / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    for tag in TAGS:
        url = RAW.format(tag=tag, rel=urllib.request.quote(rel))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "WholeBoard"}),
                                        timeout=timeout) as r:
                data = r.read(MAX_BYTES + 1)
        except Exception:  # noqa: BLE001 - 404 on this tag, try the next
            continue
        if len(data) > MAX_BYTES or not data.startswith(b"ISO-10303-21"):
            continue
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest
    with _lock:
        miss = _missing()
        miss[rel] = True
        (CACHE / "missing.json").write_text(json.dumps(miss, indent=1))
    return None


def prepare(pcb: Path, out_pcb: Path, download: bool = True, log=print) -> dict:
    """Copy a board with every model path made absolute, downloading models that are missing."""
    text = pcb.read_text()
    rels = sorted({r for r in (rel_path(m) for m in MODEL_RE.findall(text)) if r})
    found = {r: local(r) for r in rels}
    todo = [r for r, f in found.items() if f is None]
    downloaded = 0
    if todo and download:
        log(f"Downloading {len(todo)} 3D model{'s' if len(todo) != 1 else ''} from KiCad's library")
        with ThreadPoolExecutor(6) as ex:
            for r, f in zip(todo, ex.map(fetch, todo)):
                found[r] = f
                downloaded += f is not None

    def swap(m: re.Match) -> str:
        r = rel_path(m.group(1))
        f = found.get(r) if r else None
        return f'(model "{f.resolve().as_posix()}"' if f else m.group(0)

    out_pcb.write_text(MODEL_RE.sub(swap, text))
    missing = [r for r, f in found.items() if f is None]
    return {"models": len(rels), "found": len(rels) - len(missing), "downloaded": downloaded, "missing": missing}


# ------------------------------------------------------------------ heights
_bbox_cache: dict[str, list[float]] = {}
NUM = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"
POINT_RE = re.compile(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*" + NUM + r"\s*,\s*" + NUM + r"\s*,\s*" + NUM)


def step_bbox(f: Path) -> list[float] | None:
    """[xmin, ymin, zmin, xmax, ymax, zmax] of a STEP file's points, in mm."""
    key = f"{f}:{f.stat().st_mtime_ns}"
    if key in _bbox_cache:
        return _bbox_cache[key]
    index = CACHE / "bbox.json"
    try:
        saved = json.loads(index.read_text())
    except (OSError, ValueError):
        saved = {}
    if key in saved:
        _bbox_cache[key] = saved[key]
        return saved[key]
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    with open(f, encoding="latin-1") as fh:
        for m in POINT_RE.finditer(fh.read()):
            for i in range(3):
                v = float(m.group(i + 1))
                lo[i], hi[i] = min(lo[i], v), max(hi[i], v)
    if lo[0] == math.inf:
        return None
    bb = [round(v, 4) for v in lo + hi]
    _bbox_cache[key] = bb
    with _lock:
        CACHE.mkdir(parents=True, exist_ok=True)
        saved[key] = bb
        index.write_text(json.dumps(saved))
    return bb


def _rotate(p: list[float], axis: int, deg: float) -> list[float]:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    if axis == 0:
        return [x, y * c - z * s, y * s + z * c]
    if axis == 1:
        return [x * c + z * s, y, -x * s + z * c]
    return [x * c - y * s, x * s + y * c, z]


def model_height(m: dict) -> float | None:
    """How far one footprint model rises from the board, using KiCad's offset, scale and rotation."""
    r = rel_path(m["path"])
    f = local(r) if r else None
    bb = step_bbox(f) if f else None
    if not bb:
        return None
    sc, rot, off = m.get("scale") or [1, 1, 1], m.get("rotate") or [0, 0, 0], m.get("offset") or [0, 0, 0]
    top = -math.inf
    for i in range(8):
        p = [bb[k + (3 if i >> k & 1 else 0)] * sc[k] for k in range(3)]
        # KiCad applies the rotations as Rz(-z) Ry(-y) Rx(-x), innermost first.
        p = _rotate(_rotate(_rotate(p, 0, -rot[0]), 1, -rot[1]), 2, -rot[2])
        top = max(top, p[2] + off[2])
    return round(max(top, 0.1), 2)


def footprint_height(fp: dict) -> float | None:
    """The tallest visible model of a footprint from geometry.read, or None if none is on disk."""
    hs = [h for h in (model_height(m) for m in fp.get("models") or [] if not m.get("hide")) if h is not None]
    return max(hs) if hs else None


# ------------------------------------------------------------------ EasyEDA, for parts KiCad has no model for
EASYEDA_PART = "https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"
EASYEDA_OBJ = "https://modules.easyeda.com/3dmodel/{uuid}"


def _easyeda_index() -> dict:
    try:
        return json.loads((CACHE / "easyeda.json").read_text())
    except (OSError, ValueError):
        return {}


def easyeda_height(lcsc: str, download: bool = True, timeout: float = 30) -> float | None:
    """A part's height from the 3D model on its LCSC/EasyEDA page: the model's name (…-H1.4-…) or its mesh."""
    lcsc = (lcsc or "").strip().upper()
    if not re.fullmatch(r"C\d+", lcsc):
        return None
    index = _easyeda_index()
    if lcsc in index:
        return (index[lcsc] or {}).get("height")
    if not download:
        return None
    entry = None
    try:
        req = urllib.request.Request(EASYEDA_PART.format(lcsc=lcsc), headers={"User-Agent": "WholeBoard"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        shapes = (((data.get("result") or {}).get("packageDetail") or {}).get("dataStr") or {}).get("shape") or []
        attrs = None
        for s in shapes:
            if s.startswith("SVGNODE~"):
                a = json.loads(s.split("~", 1)[1]).get("attrs") or {}
                if a.get("c_etype") == "outline3D":
                    attrs = a
        if attrs:
            title = attrs.get("title", "")
            m = re.search(r"-H(\d+(?:\.\d+)?)(?:-|$)", title)
            h = float(m.group(1)) if m else _obj_height(attrs.get("uuid", ""), timeout)
            if h and 0.05 < h < 80:
                entry = {"height": round(h, 2), "model": title}
    except Exception:  # noqa: BLE001 - offline or the part has no model: remember and move on
        return None
    with _lock:
        index = _easyeda_index()
        index[lcsc] = entry
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / "easyeda.json").write_text(json.dumps(index, indent=1))
    return entry and entry["height"]


def _obj_height(uuid: str, timeout: float) -> float | None:
    if not re.fullmatch(r"[0-9a-f]{32}", uuid):
        return None
    with urllib.request.urlopen(urllib.request.Request(EASYEDA_OBJ.format(uuid=uuid),
                                                       headers={"User-Agent": "WholeBoard"}), timeout=timeout) as r:
        text = r.read(MAX_BYTES).decode("latin-1")
    zs = [float(line.split()[3]) for line in text.splitlines() if line.startswith("v ") and len(line.split()) >= 4]
    if not zs:
        return None
    # z = 0 is the board surface; pins of through-hole parts reach below it.
    return max(zs) if min(zs) < -0.3 else max(zs) - min(zs)


def part_height(fp: dict, lcsc: str | None = None, download: bool = False) -> tuple[float | None, str]:
    """(height, source) for a footprint: KiCad's model first, then EasyEDA's by LCSC number."""
    h = footprint_height(fp)
    if h is not None:
        return h, "KiCad 3D model"
    h = easyeda_height(lcsc, download=download) if lcsc else None
    return (h, "EasyEDA 3D model") if h is not None else (None, "")


def mode(cfg: dict | None = None) -> str:
    """'auto' downloads missing models, 'local' uses installed ones only, 'off' renders bare boards."""
    m = ((cfg or config.load()).get("tools") or {}).get("models3d") or "auto"
    return m if m in ("auto", "local", "off") else "auto"
