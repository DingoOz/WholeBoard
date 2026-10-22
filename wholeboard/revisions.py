"""Revision history: a summary of every build, comparisons between two designs, and restoring one."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .project import Project


def _spec(rdir: Path) -> dict | None:
    try:
        return json.loads((rdir / "design.json").read_text())
    except (OSError, ValueError):
        return None


def rev_dirs(p: Project) -> list[tuple[int, Path]]:
    out = []
    for d in p.root.glob("rev*"):
        m = re.fullmatch(r"rev(\d+)", d.name)
        if m and d.is_dir() and (d / "design.json").exists():
            out.append((int(m.group(1)), d))
    return sorted(out)


def record(p: Project, rev: int, result: dict, started: float) -> dict:
    """Write rev<N>/summary.json after a build (successful or not)."""
    rdir = p.root / f"rev{rev}"
    spec = _spec(rdir) or {}
    b = spec.get("board") or {}
    try:
        q = json.loads((rdir / "quote.json").read_text())
    except (OSError, ValueError):
        q = None
    prev = None
    for n, d in reversed(rev_dirs(p)):
        if n < rev:
            prev = _spec(d)
            break
    s = {
        "revision": rev, "time": time.time(), "build_s": round(time.time() - started, 1),
        "ok": bool(result.get("ok")), "stage": result.get("stage"),
        "errors": [str(e) for e in (result.get("errors") or [])][:6],
        "board": {"width_mm": b.get("width_mm"), "height_mm": b.get("height_mm"), "layers": b.get("layers")},
        "parts": len(spec.get("components") or []), "nets": len(spec.get("nets") or []),
        "total": (q or {}).get("total"), "per_board": (q or {}).get("per_board"),
        "image": f"rev{rev}/render-iso.png" if (rdir / "render-iso.png").exists() else None,
        "changes": brief(compare(prev, spec)) if prev else "first design",
        "by": result.get("by") or "",
    }
    ke = result.get("kicad_edits")
    if ke:
        s["kicad_edits"] = {k: ke.get(k) for k in ("hand_placed", "held", "nets", "new")}
    (rdir / "summary.json").write_text(json.dumps(s, indent=1))
    return s


def listing(p: Project) -> list[dict]:
    out = []
    for n, d in rev_dirs(p):
        try:
            s = json.loads((d / "summary.json").read_text())
        except (OSError, ValueError):
            spec = _spec(d) or {}
            s = {"revision": n, "time": (d / "design.json").stat().st_mtime, "ok": None,
                 "parts": len(spec.get("components") or []), "nets": len(spec.get("nets") or []),
                 "board": {k: (spec.get("board") or {}).get(k) for k in ("width_mm", "height_mm", "layers")},
                 "image": f"rev{n}/render-iso.png" if (d / "render-iso.png").exists() else None, "changes": ""}
        s["current"] = n == p.state.get("revision")
        out.append(s)
    return out


def compare(a: dict | None, b: dict | None) -> dict:
    """What changed from design a to design b: parts, values, footprints, LCSC numbers, nets, board."""
    a, b = a or {}, b or {}
    ca = {c["ref"]: c for c in a.get("components") or []}
    cb = {c["ref"]: c for c in b.get("components") or []}
    changed = []
    for r in sorted(set(ca) & set(cb), key=_ref_key):
        diffs = {k: [ca[r].get(k), cb[r].get(k)] for k in ("value", "footprint", "lcsc", "symbol")
                 if (ca[r].get(k) or "") != (cb[r].get(k) or "")}
        pa, pb = ca[r].get("placement") or {}, cb[r].get("placement") or {}
        if pa != pb and (pa or pb):
            diffs["placement"] = [pa or None, pb or None]
        if diffs:
            changed.append({"ref": r, "changes": diffs})
    na = {n["name"]: set(n.get("pins") or []) for n in a.get("nets") or []}
    nb = {n["name"]: set(n.get("pins") or []) for n in b.get("nets") or []}
    net_changes = []
    for n in sorted(set(na) & set(nb)):
        if na[n] != nb[n]:
            net_changes.append({"net": n, "added": sorted(nb[n] - na[n]), "removed": sorted(na[n] - nb[n])})
    ba, bb = a.get("board") or {}, b.get("board") or {}
    board = {k: [ba.get(k), bb.get(k)] for k in sorted(set(ba) | set(bb)) if ba.get(k) != bb.get(k)}
    rules = {k: [(a.get("rules") or {}).get(k), (b.get("rules") or {}).get(k)]
             for k in sorted(set(a.get("rules") or {}) | set(b.get("rules") or {}))
             if (a.get("rules") or {}).get(k) != (b.get("rules") or {}).get(k)}
    return {"parts_added": [_part(cb[r]) for r in sorted(set(cb) - set(ca), key=_ref_key)],
            "parts_removed": [_part(ca[r]) for r in sorted(set(ca) - set(cb), key=_ref_key)],
            "parts_changed": changed,
            "nets_added": [{"net": n, "pins": sorted(nb[n])} for n in sorted(set(nb) - set(na))],
            "nets_removed": [{"net": n, "pins": sorted(na[n])} for n in sorted(set(na) - set(nb))],
            "nets_changed": net_changes, "board": board, "rules": rules}


def brief(d: dict) -> str:
    """One line for a comparison, e.g. '+2 parts, R3 value 10k → 4k7, board 40 → 45 mm wide'."""
    bits = []
    if d["parts_added"]:
        bits.append(f"+{len(d['parts_added'])} part{'s' if len(d['parts_added']) != 1 else ''} "
                    f"({', '.join(x['ref'] for x in d['parts_added'][:4])})")
    if d["parts_removed"]:
        bits.append(f"−{len(d['parts_removed'])} part{'s' if len(d['parts_removed']) != 1 else ''} "
                    f"({', '.join(x['ref'] for x in d['parts_removed'][:4])})")
    for c in d["parts_changed"][:4]:
        k, (old, new) = next((k, v) for k, v in c["changes"].items() if k != "placement") \
            if any(k != "placement" for k in c["changes"]) else ("placement", (None, None))
        bits.append(f"{c['ref']} moved" if k == "placement" else f"{c['ref']} {k} {old or '–'} → {new or '–'}")
    if len(d["parts_changed"]) > 4:
        bits.append(f"{len(d['parts_changed']) - 4} more parts changed")
    nn = len(d["nets_added"]) + len(d["nets_removed"]) + len(d["nets_changed"])
    if nn:
        bits.append(f"{nn} net{'s' if nn != 1 else ''} changed")
    for k, (old, new) in list(d["board"].items())[:3]:
        if not isinstance(old, (dict, list)) and not isinstance(new, (dict, list)):
            bits.append(f"board {k.replace('_mm', '').replace('_', ' ')} {old} → {new}")
        else:
            bits.append(f"board {k.replace('_', ' ')} changed")
    if d["rules"]:
        bits.append("design rules changed")
    return ", ".join(bits) or "no design changes (rebuilt)"


def _part(c: dict) -> dict:
    return {k: c.get(k) for k in ("ref", "value", "footprint", "lcsc")}


def _ref_key(ref: str):
    m = re.match(r"([A-Za-z_]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def load(p: Project, rev: int) -> dict:
    spec = _spec(p.root / f"rev{int(rev)}")
    if spec is None:
        raise KeyError(f"revision {rev} not found")
    return spec
