"""Find KiCad's footprint and symbol libraries, search them and read footprints.

Order of search: the path in settings, KiCad's environment variables, the usual
install folders on Linux, macOS and Windows, then WholeBoard's own copy
(fetched from the KiCad Docker image or from KiCad's GitLab release archive).
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, sexpr
from ..sexpr import find, find_all, num

LIB_CACHE = config.APP_DIR / "kicad-libs"
GITLAB = "https://gitlab.com/kicad/libraries/{repo}/-/archive/{tag}/{repo}-{tag}.tar.gz"
DEFAULT_TAG = "10.0.6"


def _candidates(kind: str) -> list[Path]:
    """kind: 'footprints' or 'symbols'."""
    env_names = {
        "footprints": ["KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR"],
        "symbols": ["KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR"],
    }[kind]
    out = [Path(os.environ[e]) for e in env_names if os.environ.get(e)]
    system = platform.system()
    if system == "Darwin":
        out += [Path(f"/Applications/KiCad/KiCad.app/Contents/SharedSupport/{kind}"),
                Path.home() / f"Applications/KiCad/KiCad.app/Contents/SharedSupport/{kind}"]
    elif system == "Windows":
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
            for ver in ("10.0", "9.0", "8.0"):
                out.append(Path(root) / "KiCad" / ver / "share" / "kicad" / kind)
    else:
        out += [Path(f"/usr/share/kicad/{kind}"), Path(f"/usr/local/share/kicad/{kind}"),
                Path(f"/var/lib/flatpak/app/org.kicad.KiCad/current/active/files/share/kicad/{kind}"),
                Path.home() / f".local/share/flatpak/app/org.kicad.KiCad/current/active/files/share/kicad/{kind}"]
    out.append(LIB_CACHE / kind)
    return out


def _valid(kind: str, p: Path) -> bool:
    if not p.is_dir():
        return False
    pattern = "*.pretty" if kind == "footprints" else "*.kicad_sym"
    return next(p.glob(pattern), None) is not None


def locate(kind: str, cfg: dict | None = None) -> Path | None:
    cfg = cfg or config.load()
    key = "footprint_dir" if kind == "footprints" else "symbol_dir"
    configured = cfg["tools"].get(key)
    if configured and _valid(kind, Path(configured)):
        return Path(configured)
    for p in _candidates(kind):
        if _valid(kind, p):
            return p
    return None


# ---------------------------------------------------------------- fetching libraries

def fetch_from_docker(image: str = f"kicad/kicad:{DEFAULT_TAG}", log=print) -> bool:
    """Copy footprints and symbols out of the KiCad Docker image."""
    docker = shutil.which("docker")
    if not docker:
        return False
    LIB_CACHE.mkdir(parents=True, exist_ok=True)
    name = "wholeboard-libcopy"
    run = lambda *a: subprocess.run([docker, *a], capture_output=True, text=True)  # noqa: E731
    run("rm", "-f", name)
    r = run("create", "--name", name, image)
    if r.returncode != 0:
        log(f"docker create failed: {r.stderr.strip()}")
        return False
    try:
        for kind in ("footprints", "symbols"):
            dst = LIB_CACHE / kind
            if dst.exists():
                shutil.rmtree(dst)
            log(f"Copying KiCad {kind} from {image} ...")
            r = run("cp", f"{name}:/usr/share/kicad/{kind}", str(dst))
            if r.returncode != 0:
                log(f"docker cp failed: {r.stderr.strip()}")
                return False
    finally:
        run("rm", "-f", name)
    _INDEX.clear()
    return True


def fetch_from_gitlab(tag: str = DEFAULT_TAG, log=print) -> bool:
    """Download the KiCad footprint and symbol libraries for a release from GitLab."""
    LIB_CACHE.mkdir(parents=True, exist_ok=True)
    for kind, repo in (("footprints", "kicad-footprints"), ("symbols", "kicad-symbols")):
        url = GITLAB.format(repo=repo, tag=tag)
        log(f"Downloading {url} ...")
        with tempfile.TemporaryDirectory() as tmp:
            with urllib.request.urlopen(url, timeout=600) as resp:
                data = resp.read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                try:
                    tf.extractall(tmp, filter="data")
                except TypeError:  # Python without extraction filters
                    root_tmp = Path(tmp).resolve()
                    safe = [m for m in tf.getmembers() if not m.issym() and not m.islnk()
                            and (root_tmp / m.name).resolve().is_relative_to(root_tmp)]
                    tf.extractall(tmp, members=safe)
            src = Path(tmp) / f"{repo}-{tag}"
            dst = LIB_CACHE / kind
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
    _INDEX.clear()
    return True


# ---------------------------------------------------------------- index and search

_INDEX: dict[str, list[str]] = {}
_INDEX_LOCK = threading.Lock()


def footprint_index(cfg: dict | None = None) -> list[str]:
    """All footprints as 'Library:Name'."""
    with _INDEX_LOCK:
        if "fp" in _INDEX:
            return _INDEX["fp"]
        root = locate("footprints", cfg)
        names: list[str] = []
        if root:
            for lib in sorted(root.glob("*.pretty")):
                libname = lib.name[: -len(".pretty")]
                for f in sorted(lib.glob("*.kicad_mod")):
                    names.append(f"{libname}:{f.stem}")
        _INDEX["fp"] = names
        return names


def symbol_index(cfg: dict | None = None) -> list[str]:
    """All symbols as 'Library:Name' (read from the .kicad_sym files without a full parse)."""
    with _INDEX_LOCK:
        if "sym" in _INDEX:
            return _INDEX["sym"]
        root = locate("symbols", cfg)
        names: list[str] = []
        if root:
            cache = LIB_CACHE / f"symbol-index-{hashlib.sha1(str(root).encode()).hexdigest()[:12]}.json"
            if cache.exists():
                names = json.loads(cache.read_text())
            else:
                pat = re.compile(r'^\t\(symbol "([^"]+)"', re.M)
                for f in sorted(root.glob("*.kicad_sym")):
                    for m in pat.finditer(f.read_text(encoding="utf-8", errors="replace")):
                        names.append(f"{f.stem}:{m.group(1)}")
                LIB_CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(names))
        _INDEX["sym"] = names
        return names


def _search(names: list[str], query: str, limit: int) -> list[str]:
    tokens = [t for t in re.split(r"[\s,]+", query.lower()) if t]
    if not tokens:
        return names[:limit]
    scored = []
    for n in names:
        low = n.lower()
        if all(t in low for t in tokens):
            # shorter, more exact names first
            bonus = 0 if low.split(":", 1)[-1] == tokens[-1] else 1
            scored.append((bonus, len(n), n))
    if not scored and len(tokens) > 1:
        # nothing has every word: rank by how many words match
        need = max(1, (len(tokens) + 1) // 2)
        partial = []
        for n in names:
            low = n.lower()
            hits = sum(t in low for t in tokens)
            if hits >= need:
                partial.append((-hits, len(n), n))
        partial.sort()
        return [s[2] for s in partial[:limit]]
    scored.sort()
    return [s[2] for s in scored[:limit]]


def search_footprints(query: str, limit: int = 40) -> list[str]:
    return _search(footprint_index(), query, limit)


def search_symbols(query: str, limit: int = 40) -> list[str]:
    return _search(symbol_index(), query, limit)


# ---------------------------------------------------------------- footprints

@dataclass
class Pad:
    name: str
    kind: str          # smd | thru_hole | np_thru_hole | connect
    shape: str
    x: float
    y: float
    w: float
    h: float
    angle: float = 0.0
    drill: float = 0.0
    layers: list[str] = field(default_factory=list)


@dataclass
class Footprint:
    lib_id: str
    node: list
    pads: list[Pad]
    courtyard: tuple[float, float, float, float]  # minx, miny, maxx, maxy (relative)
    smd: bool
    description: str = ""

    @property
    def pad_names(self) -> list[str]:
        seen: list[str] = []
        for p in self.pads:
            if p.name and p.name not in seen:
                seen.append(p.name)
        return seen

    def summary(self) -> dict:
        minx, miny, maxx, maxy = self.courtyard
        return {
            "footprint": self.lib_id,
            "description": self.description,
            "size_mm": [round(maxx - minx, 2), round(maxy - miny, 2)],
            "mount": "SMD" if self.smd else "through-hole",
            "pads": self.pad_names,
        }


_FP_CACHE: dict[str, Footprint] = {}


def footprint_path(lib_id: str) -> Path | None:
    if ":" not in lib_id:
        return None
    lib, name = lib_id.split(":", 1)
    root = locate("footprints")
    if not root:
        return None
    p = root / f"{lib}.pretty" / f"{name}.kicad_mod"
    return p if p.exists() else None


def load_footprint(lib_id: str) -> Footprint | None:
    if lib_id in _FP_CACHE:
        return _FP_CACHE[lib_id]
    path = footprint_path(lib_id)
    if not path:
        return None
    node = sexpr.parse(path.read_text(encoding="utf-8"))
    pads: list[Pad] = []
    for p in find_all(node, "pad"):
        name = p[1] if len(p) > 1 and isinstance(p[1], str) else ""
        at = find(p, "at") or ["at", 0, 0]
        size = find(p, "size") or ["size", 0, 0]
        drill = find(p, "drill")
        layers = find(p, "layers") or ["layers"]
        d = 0.0
        if drill:
            vals = [num(v) for v in drill[1:] if not isinstance(v, list) and str(v) != "oval"]
            d = vals[0] if vals else 0.0
        pads.append(Pad(
            name=str(name), kind=str(p[2]), shape=str(p[3]),
            x=num(at[1]), y=num(at[2]), angle=num(at[3]) if len(at) > 3 else 0.0,
            w=num(size[1]), h=num(size[2]), drill=d, layers=[str(x) for x in layers[1:]],
        ))
    xs: list[float] = []
    ys: list[float] = []
    for g in node:
        if sexpr.tag(g) in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
            layer = find(g, "layer")
            if layer and str(layer[1]) in ("F.CrtYd", "B.CrtYd"):
                c, e = find(g, "center"), find(g, "end")
                if sexpr.tag(g) == "fp_circle" and c and e:      # a circle is its centre and one point on it
                    r = math.hypot(num(e[1]) - num(c[1]), num(e[2]) - num(c[2]))
                    xs += [num(c[1]) - r, num(c[1]) + r]
                    ys += [num(c[2]) - r, num(c[2]) + r]
                    continue
                for pt in sexpr.walk(g):
                    if sexpr.tag(pt) in ("start", "end", "xy", "mid", "center"):
                        xs.append(num(pt[1]))
                        ys.append(num(pt[2]))
    if not xs:
        for p in pads:
            xs += [p.x - p.w / 2, p.x + p.w / 2]
            ys += [p.y - p.h / 2, p.y + p.h / 2]
    court = (min(xs), min(ys), max(xs), max(ys)) if xs else (-1, -1, 1, 1)
    attr = find(node, "attr")
    smd = bool(attr and Sym_in(attr, "smd")) or all(p.kind == "smd" for p in pads if p.kind != "np_thru_hole")
    descr = find(node, "descr")
    fp = Footprint(lib_id, node, pads, court, smd, str(descr[1]) if descr else "")
    _FP_CACHE[lib_id] = fp
    return fp


def Sym_in(node: list, word: str) -> bool:
    return any(isinstance(x, sexpr.Sym) and str(x) == word for x in node)


def status(cfg: dict | None = None) -> dict:
    fp = locate("footprints", cfg)
    sym = locate("symbols", cfg)
    return {
        "footprint_dir": str(fp) if fp else None,
        "symbol_dir": str(sym) if sym else None,
        "footprints": len(footprint_index(cfg)) if fp else 0,
    }
