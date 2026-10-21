"""Turn an existing KiCad project (schematic, and the board if there is one) into a WholeBoard spec.

The schematic's netlist gives the parts and connections; the board, when present, gives the
outline, layer count, mounting holes and where each part sits (used as placement hints).
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path, PurePosixPath

from .. import design, sexpr
from ..sexpr import find, find_all
from . import cli, geometry, libs

LCSC_FIELDS = ("lcsc", "lcsc part", "lcsc part #", "lcsc#", "jlcpcb part", "jlcpcb part #", "jlc", "lcsc_part",
               "supplier part number", "jlcpcb")
MPN_FIELDS = ("mpn", "manufacturer part number", "mfr part", "mfr. part #", "part number", "manf#")
POWER_RE = re.compile(r"^(GND\w*|AGND|DGND|PGND|VCC\w*|VDD\w*|VSS\w*|VBUS|VBAT\w*|VIN|VSYS|\+?\d+V\d*\w*|V\d+V\d*|\+V\w*)$",
                       re.IGNORECASE)
MAX_FILES = 400
MAX_BYTES = 80_000_000


def save_upload(files: list[tuple[str, bytes]], dest: Path) -> list[Path]:
    """Write uploaded files (zips are unpacked) into dest. Paths outside dest are refused."""
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    total = 0

    def put(name: str, data: bytes) -> None:
        nonlocal total
        parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "/")]
        if not parts or any(p == ".." for p in parts) or ":" in parts[0]:
            raise ValueError(f"refusing the file name {name!r}")
        total += len(data)
        if total > MAX_BYTES or len(out) >= MAX_FILES:
            raise ValueError("the upload is too big (80 MB and 400 files at most)")
        f = dest.joinpath(*parts)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        out.append(f)

    for name, data in files:
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        if info.file_size > MAX_BYTES:
                            raise ValueError("a file in the zip is too big")
                        put(info.filename, z.read(info))
        else:
            put(PurePosixPath(name.replace("\\", "/")).name, data)
    return out


def _root_schematic(folder: Path) -> Path | None:
    schs = [s for s in folder.rglob("*.kicad_sch") if "-backups" not in str(s)]
    if not schs:
        return None
    for pro in folder.rglob("*.kicad_pro"):
        s = pro.with_suffix(".kicad_sch")
        if s.exists():
            return s
    # The root sheet is the one no other sheet includes.
    named = {s.name for s in schs}
    included = set()
    for s in schs:
        included |= set(re.findall(r'\(property\s+"Sheet\s?file"\s+"([^"]+)"', s.read_text(errors="replace"))) & named
    roots = [s for s in schs if s.name not in included]
    return max(roots or schs, key=lambda s: s.stat().st_size)


def _board(folder: Path, sch: Path | None) -> Path | None:
    if sch and sch.with_suffix(".kicad_pcb").exists():
        return sch.with_suffix(".kicad_pcb")
    pcbs = [p for p in folder.rglob("*.kicad_pcb") if "-backups" not in str(p)]
    return max(pcbs, key=lambda p: p.stat().st_size) if pcbs else None


def _netlist(sch: Path) -> list:
    out = sch.with_name(sch.stem + ".wb-import.net")
    r = cli.run(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)], cwd=sch.parent)
    if not out.exists():
        raise ValueError(f"KiCad could not read the schematic: {(r.stderr or r.stdout).strip()[:400]}")
    return sexpr.parse(out.read_text())


def _field(comp: list, names: tuple[str, ...]) -> str:
    fields = find(comp, "fields")
    for f in find_all(fields, "field") if fields else []:
        n = find(f, "name")
        if n and str(n[1]).strip().lower() in names and len(f) > 2 and str(f[2]).strip():
            return str(f[2]).strip()
    for p in find_all(comp, "property"):
        n, v = find(p, "name"), find(p, "value")
        if n and v and str(n[1]).strip().lower() in names and str(v[1]).strip():
            return str(v[1]).strip()
    return ""


def _net_name(raw: str, used: set[str]) -> str:
    name = raw.strip().lstrip("/")
    name = re.sub(r"^Net-\((.+)\)$", r"N_\1", name)
    name = re.sub(r"[^A-Za-z0-9_+.\-]+", "_", name).strip("_") or "NET"
    base, i = name, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def _pcb_layers(pcb: Path) -> int:
    tree = sexpr.parse(pcb.read_text())
    lay = find(tree, "layers")
    n = sum(1 for l in lay[1:] if isinstance(l, list) and len(l) > 1 and str(l[1]).endswith(".Cu")) if lay else 2
    return n if n in (1, 2, 4, 6) else (2 if n < 4 else 4 if n < 6 else 6)


def to_spec(folder: Path, name: str = "", log=print) -> dict:
    """{"spec", "notes", "warnings", "files"} for a KiCad project in folder."""
    sch, notes = _root_schematic(folder), []
    pcb = _board(folder, sch)
    if not sch and not pcb:
        raise ValueError("no .kicad_sch or .kicad_pcb file was found in the upload")
    comps: dict[str, dict] = {}
    nets: list[dict] = []
    if sch:
        log(f"Reading the schematic {sch.name} with KiCad")
        tree = _netlist(sch)
        for c in find_all(find(tree, "components") or [], "comp"):
            ref = str(find(c, "ref")[1])
            if ref.startswith("#"):
                continue
            item = {"ref": ref, "value": str((find(c, "value") or ["", ""])[1]),
                    "footprint": str((find(c, "footprint") or ["", ""])[1])}
            desc = find(c, "description")
            if desc and len(desc) > 1 and str(desc[1]).strip():
                item["description"] = str(desc[1]).strip()[:200]
            src = find(c, "libsource")
            if src:
                lib, part = find(src, "lib"), find(src, "part")
                if lib and part:
                    item["symbol"] = f"{lib[1]}:{part[1]}"
            lcsc = _field(c, LCSC_FIELDS).upper()
            if re.fullmatch(r"C\d+", lcsc):
                item["lcsc"] = lcsc
            mpn = _field(c, MPN_FIELDS)
            if mpn:
                item["mpn"] = mpn
            if any(str((find(p, "name") or ["", ""])[1]).lower() in ("dnp", "exclude_from_board") for p in find_all(c, "property")):
                item["dnp"] = True
            comps[ref] = item
        used: set[str] = set()
        for n in find_all(find(tree, "nets") or [], "net"):
            pins = [f"{find(nd, 'ref')[1]}.{find(nd, 'pin')[1]}" for nd in find_all(n, "node")
                    if not str(find(nd, "ref")[1]).startswith("#")]
            raw = str(find(n, "name")[1])
            if len(pins) < 2:
                if len(pins) == 1 and raw.lower().startswith("unconnected-"):
                    ref, pad = pins[0].split(".", 1)
                    if ref in comps:
                        comps[ref].setdefault("no_connect", []).append(pad)
                continue
            nm = _net_name(raw, used)
            nets.append({"name": nm, "class": "power" if POWER_RE.match(nm) else "signal", "pins": pins})
        (sch.with_name(sch.stem + ".wb-import.net")).unlink(missing_ok=True)

    board: dict = {"layers": 2}
    if pcb:
        log(f"Reading the board {pcb.name}")
        geo = geometry.read(pcb)
        board["layers"] = _pcb_layers(pcb)
        bb = geo.get("bbox")
        if bb:
            board["width_mm"], board["height_mm"] = round(bb[2] - bb[0], 2), round(bb[3] - bb[1], 2)
            holes = []
            for f in geo["footprints"]:
                x, y = round(f["x"] - bb[0], 2), round(f["y"] - bb[1], 2)
                if "mountinghole" in f["lib"].lower():
                    drills = [p["drill"] for p in geo["pads"] if p["ref"] == f["ref"] and p.get("drill")]
                    holes.append((x, y, max(drills) if drills else 3.2))
                    continue
                c = comps.get(f["ref"])
                if c is None and not sch:
                    c = comps.setdefault(f["ref"], {"ref": f["ref"], "value": "", "footprint": f["lib"]})
                if c is None:
                    continue
                if not c.get("footprint"):
                    c["footprint"] = f["lib"]
                c["placement"] = {"x_mm": x, "y_mm": y, "rotation": round(f.get("rot", 0), 1),
                                  "side": "bottom" if f["side"] == "B.Cu" else "top"}
            if holes:
                w, h = board["width_mm"], board["height_mm"]
                inset = min(min(x, y, w - x, h - y) for x, y, _ in holes)
                board["mounting_holes"] = {"diameter_mm": holes[0][2], "inset_mm": round(max(inset, 2.0), 2)}
                notes.append(f"{len(holes)} mounting holes found; WholeBoard puts them in the corners "
                             f"{board['mounting_holes']['inset_mm']} mm in.")
        else:
            notes.append("The board has no Edge.Cuts outline, so the size is a guess.")
        if not sch:
            notes.append("Only a board file was given, so the connections are not known; send the schematic too.")
    if "width_mm" not in board:
        n = max(1, len(comps))
        side = round(max(20.0, min(150.0, (n * 40) ** 0.5 * 2)), 0)
        board.update({"width_mm": side, "height_mm": side})
        notes.append(f"No board outline, so the board starts at {side:g} × {side:g} mm.")

    missing_fp = [r for r, c in comps.items() if not c.get("footprint")]
    custom = sorted({c["footprint"] for c in comps.values() if c.get("footprint") and not libs.load_footprint(c["footprint"])})
    if missing_fp:
        notes.append(f"No footprint for {', '.join(missing_fp[:12])}.")
    if custom:
        notes.append(f"{len(custom)} footprint{'s are' if len(custom) != 1 else ' is'} not in KiCad's standard "
                     "libraries (" + ", ".join(custom[:6]) + (", …" if len(custom) > 6 else "") +
                     "); they need standard equivalents.")
    no_lcsc = [r for r, c in comps.items() if not c.get("lcsc") and not c.get("dnp")]
    if no_lcsc:
        notes.append(f"{len(no_lcsc)} parts have no LCSC number: {', '.join(no_lcsc[:15])}" + ("…" if len(no_lcsc) > 15 else ""))
    title = name or (sch or pcb).stem
    spec = {"name": re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_") or "imported",
            "summary": f"Imported from the KiCad project {title}.", "board": board,
            "components": sorted(comps.values(), key=lambda c: _ref_key(c["ref"])), "nets": nets}
    rep = design.validate(spec)
    return {"spec": spec, "notes": notes, "errors": rep.errors, "warnings": rep.warnings,
            "files": {"schematic": sch.name if sch else None, "board": pcb.name if pcb else None},
            "counts": {"components": len(comps), "nets": len(nets)}}


def _ref_key(ref: str):
    m = re.match(r"([A-Za-z_]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)
