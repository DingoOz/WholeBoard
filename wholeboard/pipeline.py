"""Turn a design spec into a checked, routed, quoted board, step by step."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from . import bringup, case as stock_case, checks, config, design, enclosure, firmware, fwbuild, ibom, impedance, models3d, ngspice, power, revisions, router, sim
from .jlc import fabs as jfabs, parts as jparts, quote as jquote, savings as jsavings
from .kicad import board, cli, geometry, libs, schematic
from .project import Project

ROUTE_LOCK = threading.Lock()   # one routing job at a time (CPU heavy, one viewer port)


class Cancelled(Exception):
    pass


def _check(p: Project):
    if p.cancel.is_set():
        raise Cancelled()


def _board_visual(p: Project, step: str, pcb: Path, title: str) -> dict:
    geo = geometry.read(pcb)
    out = pcb.parent / f"{pcb.stem}.geo.json"
    out.write_text(json.dumps(geo))
    p.visual(step, "board", title, url=p.file_url(out))
    return geo


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def check_parts(spec: dict) -> list[str]:
    """Stock and package sanity checks against JLCPCB's library (warnings only)."""
    notes = []
    for c in design.normalise(spec)["components"]:
        code = c.get("lcsc")
        if not code:
            continue
        try:
            part = jparts.lookup(str(code))
        except Exception:  # noqa: BLE001
            part = None
        if not part:
            notes.append(f"{c['ref']}: LCSC part {code} was not found at JLCPCB")
            continue
        if part["stock"] <= 0:
            notes.append(f"{c['ref']}: {code} ({part['mpn']}) is out of stock at JLCPCB")
        pkg = _norm(part.get("package") or "")
        fpn = _norm(c["footprint"].split(":")[-1])
        size_codes = re.findall(r"\b(0201|0402|0603|0805|1206|1210|2512)\b", part.get("package") or "")
        if size_codes and size_codes[0] not in c["footprint"]:
            notes.append(f"{c['ref']}: {code} is a {part['package']} part but the footprint is "
                         f"{c['footprint']}")
        elif pkg and len(pkg) >= 5 and pkg[:5] not in fpn and not any(
                tok in fpn for tok in re.findall(r"[a-z]+\d+", pkg)):
            notes.append(f"{c['ref']}: check that {code} package '{part['package']}' matches "
                         f"footprint {c['footprint']}")
    return notes


def build(p: Project, spec: dict, view_origin: str | None = None, by: str = "") -> dict:
    """Build one revision of the board and record it in the revision history."""
    started = time.time()
    try:
        res = _build(p, spec, view_origin)
    except Cancelled:
        _record(p, {"ok": False, "stage": "stopped", "by": by}, started)
        raise
    except Exception as e:  # noqa: BLE001
        _record(p, {"ok": False, "stage": "error", "errors": [str(e)], "by": by}, started)
        raise
    _record(p, {**res, "by": by}, started)
    return res


def _record(p: Project, res: dict, started: float) -> None:
    try:
        revisions.record(p, int(p.state.get("revision", 0)), res, started)
    except Exception as e:  # noqa: BLE001 - history is a convenience, never fail a build over it
        p.log("netlist", f"Could not save the revision summary: {e}")


def _build(p: Project, spec: dict, view_origin: str | None = None) -> dict:
    cfg = config.load()
    spec = design.normalise(spec)
    rev = int(p.state.get("revision", 0)) + 1
    p.state["revision"] = rev
    p.state["spec"] = spec
    p.save()
    rdir = p.root / f"rev{rev}"
    rdir.mkdir(exist_ok=True)
    (rdir / "design.json").write_text(json.dumps(spec, indent=1))
    name = spec["name"]
    result: dict = {"revision": rev}

    for key in ("netlist", "schematic", "sim", "layout", "place", "route", "drc", "outputs", "quote", "review",
                "enclosure"):
        p.step(key, "pending")

    # ---- 1. validate
    p.step("netlist", "running", "Checking footprints, pads and nets")
    rep = design.validate(spec)
    part_notes = check_parts(spec)
    p.visual("netlist", "netlist", f"Netlist, revision {rev}",
             components=[{k: c.get(k) for k in ("ref", "value", "footprint", "lcsc", "description")}
                         for c in spec["components"]],
             nets=[{"name": n["name"], "class": n.get("class"), "pins": n["pins"]} for n in spec["nets"]],
             errors=rep.errors, warnings=rep.warnings + part_notes)
    if not rep.ok:
        p.step("netlist", "error", f"{len(rep.errors)} problems")
        return {**result, "ok": False, "stage": "design check", "errors": rep.errors,
                "warnings": rep.warnings, "part_notes": part_notes}
    p.step("netlist", "done", f"{len(spec['components'])} parts, {len(spec['nets'])} nets"
           + (f", {len(part_notes)} part notes" if part_notes else ""))
    result["warnings"] = rep.warnings
    result["part_notes"] = part_notes
    _check(p)

    # ---- 2. schematic
    p.step("schematic", "running", "Drawing the schematic")
    try:
        sch = schematic.build(spec, rdir)
        svg_dir = rdir / "schematic-svg"
        cli.run(["sch", "export", "svg", "--exclude-drawing-sheet", "-o", str(svg_dir), sch["sch"]],
                cwd=rdir)
        svgs = sorted(svg_dir.glob("*.svg"))
        if svgs:
            if sch.get("extent"):
                schematic.crop_svg(svgs[0], sch["extent"])
            p.visual("schematic", "svg", "Schematic", url=p.file_url(svgs[0]))
        erc = None
        try:
            erc = cli.erc(Path(sch["sch"]))
            p.visual("schematic", "erc", "Electrical rules check", report=erc)
            result["erc"] = {"errors": erc["errors"][:15], "warnings": erc["warnings"][:10]}
        except Exception as e:  # noqa: BLE001
            p.log("schematic", f"ERC did not run: {e}")
        if erc and erc["errors"]:
            p.step("schematic", "warn", f"ERC: {len(erc['errors'])} errors, {len(erc['warnings'])} warnings")
        else:
            p.step("schematic", "done", ("ERC clean. " if erc else "") + "; ".join(sch["notes"])[:200])
    except Exception as e:  # noqa: BLE001 - the schematic is a view; the board can still be built
        p.step("schematic", "warn", f"Schematic not drawn: {e}")
    _check(p)

    # ---- 2b. circuit simulation
    sim_res = run_sim(p, spec, rdir)
    result["simulation"] = sim_summary(sim_res)
    if sim_res.get("ran") and not sim_res["ok"] and (spec.get("simulation") or {}).get("blocking", True):
        return {**result, "ok": False, "stage": "simulation", "errors": sim_res["problems"],
                "hint": "Fix the circuit (or a wrong model with the component's \"sim\" entry, or a wrong "
                        "expectation) and submit again. Set simulation.blocking false only if a problem is a "
                        "limitation of the simulation."}
    _check(p)

    # ---- 3. board with footprints
    p.step("layout", "running", "Creating the board, outline and footprints")
    built = board.build(spec, rdir)
    pro = Path(built["pro"])
    gen = rdir / f"{name}-1-generated.kicad_pcb"
    Path(built["pcb"]).replace(gen)
    shutil.copyfile(pro, gen.with_suffix(".kicad_pro"))
    _board_visual(p, "layout", gen, "Footprints ready for placement")
    p.step("layout", "done", f"{spec['board']['width_mm']}×{spec['board']['height_mm']} mm, "
           f"{spec['board']['layers']} layers")
    _check(p)

    # ---- 4. placement
    placed = rdir / f"{name}-2-placed.kicad_pcb"
    movable = [c for c in spec["components"] if (c.get("placement") or {}).get("x_mm") is None]
    p.step("place", "running", "TraceMaker is placing the parts")
    try:
        router.place(gen, placed, log=lambda s: p.log("place", s), cancel=p.cancel,
                     mode="routable", scratch=True)
    except Exception as e:  # noqa: BLE001
        p.step("place", "error", str(e))
        return {**result, "ok": False, "stage": "placement", "errors": [str(e)]}
    _check(p)
    geo = _board_visual(p, "place", placed, "Placed parts with air wires")
    off = _off_board(geo)
    if off:
        p.step("place", "error", f"{len(off)} parts did not fit on the board")
        return {**result, "ok": False, "stage": "placement",
                "errors": [f"These parts did not fit on the board: {', '.join(off)}. "
                           "Make the board bigger or use smaller parts."]}
    p.step("place", "done", f"{len(movable)} parts placed")

    # ---- 5. routing
    routed = rdir / f"{name}-3-routed.kicad_pcb"
    with ROUTE_LOCK:
        port = None
        if cfg["tools"].get("live_viewer") and router.viewer_available(cfg):
            port = int(cfg["tools"].get("viewer_port", 8766))
            p.visual("route", "viewer", "TraceMaker live router", url=f"http://127.0.0.1:{port}/",
                     port=port, token=router.viewer_token(), live=True)
        p.step("route", "running", "TraceMaker is routing")
        try:
            rr = router.route(placed, routed, log=lambda s: p.log("route", s), cancel=p.cancel,
                              view_port=port, embed_origin=view_origin, diff_pairs=impedance.has_pairs(spec))
            if not rr["complete"] and not p.cancel.is_set():
                p.log("route", "Not every connection routed; nudging the placement and trying again.")
                eco = rdir / f"{name}-2b-eco.kicad_pcb"
                router.place(routed, eco, log=lambda s: p.log("route", s), cancel=p.cancel,
                             mode="eco", scratch=False)
                rr2 = router.route(eco, routed, log=lambda s: p.log("route", s), cancel=p.cancel,
                                   view_port=port, embed_origin=view_origin, reroute=True,
                                   diff_pairs=impedance.has_pairs(spec))
                if (rr2.get("routed") or 0) >= (rr.get("routed") or 0):
                    rr = rr2
        except Exception as e:  # noqa: BLE001
            p.step("route", "error", str(e))
            return {**result, "ok": False, "stage": "routing", "errors": [str(e)]}
    _check(p)
    _board_visual(p, "route", routed, "Routed copper")
    result["routing"] = {k: rr.get(k) for k in ("routed", "connections", "tracks", "vias", "seconds")}
    if not rr["complete"]:
        p.step("route", "error", f"{rr['routed']} of {rr['connections']} connections routed")
        return {**result, "ok": False, "stage": "routing",
                "errors": [f"Routing left {rr['connections'] - rr['routed']} of {rr['connections']} "
                           "connections open."], "failures": rr["failures"]}
    p.step("route", "done", f"{rr['routed']}/{rr['connections']} connections, {rr['vias']} vias")

    # ---- 6. pour + DRC
    p.step("drc", "running", "Adding the ground pour and running KiCad's DRC")
    final = rdir / f"{name}.kicad_pcb"
    shutil.copyfile(routed, final)
    board.add_ground_pour(final, spec)
    try:
        drc = cli.drc(final, refill=True, save=True)
    except Exception as e:  # noqa: BLE001
        p.step("drc", "error", str(e))
        return {**result, "ok": False, "stage": "drc", "errors": [str(e)]}
    _board_visual(p, "drc", final, "Final board with ground pour")
    p.visual("drc", "drc", "KiCad design rule check", report=drc)
    result["drc"] = {k: drc[k] for k in ("errors", "warnings", "unconnected", "by_type")}
    if not drc["clean"]:
        p.step("drc", "error", f"{drc['errors']} errors, {drc['unconnected']} unconnected")
        return {**result, "ok": False, "stage": "drc", "drc_errors": drc["error_list"][:20],
                "unconnected": drc["unconnected_list"][:20]}
    try:
        geo_final = geometry.read(final)
        bc = checks.run(spec, geo_final, sim_res.get("net_current_ma"), sim_res.get("supplies"))
        p.visual("drc", "checks", "Current, voltage drop and decoupling", report=bc)
        result["board_checks"] = {k: bc[k] for k in ("problems", "warnings")}
        for w in bc["problems"] + bc["warnings"]:
            p.log("drc", w)
    except Exception as e:  # noqa: BLE001
        bc = None
        p.log("drc", f"Board checks did not run: {e}")
    if spec.get("impedance"):
        try:
            imp = impedance.check(spec, geometry.read(final))
            p.visual("drc", "impedance", "Controlled-impedance traces", report=imp)
            result["impedance"] = imp
            for w in imp["warnings"]:
                p.log("drc", w)
        except Exception as e:  # noqa: BLE001
            p.log("drc", f"Impedance check did not run: {e}")
    extra = f", {len(bc['problems']) + len(bc['warnings'])} board notes" if bc and (bc["problems"] or bc["warnings"]) else ""
    p.step("drc", "warn" if bc and bc["problems"] else "done", f"clean ({drc['warnings']} warnings{extra})")
    _check(p)

    # ---- 7. renders + fab outputs
    p.step("outputs", "running", "Rendering 3D views and exporting Gerbers, BOM and CPL")
    images, render_pcb, m3 = [], final, None
    if models3d.mode() != "off":
        try:
            render_pcb = rdir / f"{name}-render3d.kicad_pcb"
            m3 = models3d.prepare(final, render_pcb, models3d.mode() == "auto", log=lambda m: p.log("outputs", m))
            result["models3d"] = m3
            _part_heights(p, spec, final, models3d.mode() == "auto")
        except Exception as e:  # noqa: BLE001
            render_pcb = final
            p.log("outputs", f"3D models were not added: {e}")
    for view in ("iso", "top", "bottom"):
        try:
            img = cli.render(render_pcb, rdir / f"render-{view}.png", view)
            images.append({"view": view, "url": p.file_url(img)})
        except Exception as e:  # noqa: BLE001
            p.log("outputs", f"3D render ({view}) failed: {e}")
    if render_pcb != final:
        for f in (render_pcb, render_pcb.with_suffix(".kicad_prl"), render_pcb.with_suffix(".kicad_pro")):
            f.unlink(missing_ok=True)
    if images:
        note = ""
        if m3 and m3["missing"]:
            note = (f"{m3['found']} of {m3['models']} 3D models shown; KiCad has none for "
                    + ", ".join(Path(r).stem for r in m3["missing"][:6]) + ".")
        elif not m3:
            note = "3D models are turned off in Settings, so parts are not shown."
        p.visual("outputs", "images", "3D renders", images=images, note=note)
    fab_dir = rdir / "fab"
    try:
        fab = cli.export_fab(final, fab_dir, int(spec["board"]["layers"]))
        rows = jquote.bom_rows(spec)
        bom = jquote.write_bom(rows, fab_dir / f"{name}-bom.csv")
        files = [{"name": Path(fab["gerber_zip"]).name, "url": p.file_url(Path(fab["gerber_zip"]))},
                 {"name": bom.name, "url": p.file_url(bom)}]
        if fab.get("pos"):
            excl = {c["ref"] for c in spec["components"] if not c.get("lcsc")}
            overrides = {c["ref"]: c["cpl"] for c in spec["components"] if isinstance(c.get("cpl"), dict)}
            cpl, fixes = cli.jlc_cpl(Path(fab["pos"]), fab_dir / f"{name}-cpl.csv", excl, overrides)
            files.append({"name": cpl.name, "url": p.file_url(cpl)})
            result["cpl_rotations"] = fixes
        try:
            page = ibom.generate(spec, geometry.read(final), fab_dir / f"{name}-assembly.html", spec.get("name"))
            files.append({"name": page.name, "url": p.file_url(page)})
        except Exception as e:  # noqa: BLE001
            p.log("outputs", f"Assembly page not written: {e}")
        exports = p.root / "exports"
        exports.mkdir(exist_ok=True)
        try:
            fw = firmware.generate(spec, rdir / "firmware")
        except Exception as e:  # noqa: BLE001
            fw = None
            p.log("outputs", f"Starter firmware was not written: {e}")
        if fw:
            fw_zip = Path(shutil.make_archive(str(exports / f"{name}-rev{rev}-firmware"), "zip", rdir / "firmware"))
            p.visual("outputs", "firmware", f"Starter firmware for {fw['mcu']} ({fw['family']})",
                     firmware={k: fw[k] for k in ("mcu", "value", "family", "env", "pins", "notes")},
                     url=p.file_url(fw_zip), name=fw_zip.name)
            result["firmware"] = {"mcu": fw["mcu"], "family": fw["family"], "pins": len(fw["pins"]),
                                  "notes": fw["notes"]}
        try:
            bu = bringup.guide(spec, sim_res, sim_res.get("power"), fw)
            md = rdir / f"{name}-bringup.md"
            md.write_text(bu["markdown"])
            shutil.copyfile(md, exports / f"{name}-rev{rev}-bringup.md")
            p.visual("outputs", "bringup", "Bring-up guide", steps=bu["steps"], test_points=bu["test_points"],
                     url=p.file_url(exports / f"{name}-rev{rev}-bringup.md"), name=f"{name}-rev{rev}-bringup.md")
        except Exception as e:  # noqa: BLE001
            p.log("outputs", f"Bring-up guide not written: {e}")
        zip_proj = shutil.make_archive(str(exports / f"{name}-rev{rev}-kicad-project"), "zip", rdir)
        files.append({"name": Path(zip_proj).name, "url": p.file_url(Path(zip_proj))})
        fixes = result.get("cpl_rotations") or []
        p.visual("outputs", "files", "Files for JLCPCB", files=files, folder=str(rdir), rotations=fixes,
                 note=(f"The CPL file turns {len(fixes)} part{'s' if len(fixes) != 1 else ''} to match JLCPCB's "
                       "library orientation (listed below). " if fixes else "") + "Check part rotations in JLCPCB's assembly preview before "
                 "ordering.")
        p.state["artifacts"] = {"folder": str(rdir), "pcb": str(final), "files": files}
        p.save()
        p.step("outputs", "done", f"{len(fab['files'])} fab files, {len(images)} renders")
    except Exception as e:  # noqa: BLE001
        p.step("outputs", "error", str(e))
        return {**result, "ok": False, "stage": "outputs", "errors": [str(e)]}
    _check(p)

    # ---- 8. quote
    result["quote"] = run_quote(p, spec)
    result["ok"] = True
    result["folder"] = str(rdir)
    _check(p)
    make_report(p)

    # ---- 9. enclosure: drilling templates for a stock case, or the printed enclosure rebuilt around the board
    if spec["board"].get("case"):
        result["case"] = run_case(p, spec, final)
        make_report(p)
    elif p.state.get("enclosure_spec"):
        result["enclosure"] = run_enclosure(p, spec, p.state["enclosure_spec"], final)
        make_report(p)
    else:
        p.step("enclosure", "pending", "Designed once the board is finished")
    return result


def _part_heights(p, spec: dict, pcb: Path, download: bool) -> None:
    """Measure every part's height from its 3D model, for the enclosure; fetch EasyEDA's if KiCad has none."""
    geo = geometry.read(pcb)
    lcsc = {c["ref"]: c.get("lcsc") for c in spec.get("components") or []}
    rows = []
    for f in geo["footprints"]:
        h, src = models3d.part_height(f, lcsc.get(f["ref"]), download)
        if h is not None:
            rows.append({"ref": f["ref"], "height_mm": h, "source": src})
    if rows:
        tallest = max(rows, key=lambda r: r["height_mm"])
        p.log("outputs", f"Measured {len(rows)} part heights from 3D models; tallest {tallest['ref']} "
                         f"{tallest['height_mm']} mm")


def run_sim(p: Project, spec: dict, rdir: Path) -> dict:
    """Simulate the design with ngspice and show the results in the "sim" step."""
    cfg = config.load()
    engine = ngspice.find(cfg)
    p.step("sim", "running", "Simulating the circuit with ngspice")

    def lookup(code):
        try:
            return jparts.lookup(code)
        except Exception:  # noqa: BLE001 - offline: ratings come from the spec only
            return None
    try:
        res = sim.simulate(spec, rdir / "sim", cfg, engine, part_info=lookup,
                           log=lambda m: p.log("sim", m), cancel=p.cancel)
    except Exception as e:  # noqa: BLE001
        p.step("sim", "warn", f"Simulation failed: {e}")
        return {"ran": False, "ok": True, "problems": [], "warnings": [str(e)]}
    plot = rdir / "sim" / "plot.json"
    plot.write_text(json.dumps({"plot": res.get("plot"), "ac": res.get("ac")}))
    shown = {k: v for k, v in res.items() if k not in ("plot", "ac")}
    p.visual("sim", "sim", "Circuit simulation", result=shown, plot=p.file_url(plot),
             netlist=p.file_url(rdir / "sim" / "circuit.cir") if (rdir / "sim" / "circuit.cir").exists() else None)
    if not res.get("ran"):
        p.step("sim", "warn", res.get("skipped") or "Not simulated")
    elif res["problems"]:
        p.step("sim", "error", f"{len(res['problems'])} problems")
    else:
        sig = res.get("signals") or []
        bits = [f"{s['net']} {s['frequency_hz']:.3g} Hz" for s in sig[:2] if s.get("frequency_hz")]
        sup = [f"{s['current_ma']:.1f} mA from {s['net']}" for s in res.get("supplies", []) if s["kind"] != "signal"]
        p.step("sim", "warn" if res["warnings"] else "done", ", ".join(sup[:2] + bits) or "Simulated")
    for w in res["problems"] + res["warnings"]:
        p.log("sim", w)
    if res.get("ran"):
        try:
            res["power"] = power.budget(spec, res)
            p.visual("sim", "power", "Power budget and heat", budget=res["power"])
            (rdir / "sim" / "power.json").write_text(json.dumps(res["power"], indent=1))
        except Exception as e:  # noqa: BLE001
            p.log("sim", f"Power budget not worked out: {e}")
    return res


def sim_summary(res: dict) -> dict:
    """What the AI needs from a simulation: no waveforms."""
    keep = ("ran", "ok", "skipped", "problems", "warnings", "assumptions", "notes", "rails", "supplies",
            "signals", "expectations")
    out = {k: res[k] for k in keep if k in res}
    out["parts"] = [r for r in res.get("parts", []) if r["kind"] not in ("capacitor",) or r.get("rating_v")][:40]
    if res.get("power"):
        out["power_budget"] = power.brief(res["power"])
    return out


def run_enclosure(p: Project, spec: dict, enc: dict, pcb: Path | None = None) -> dict:
    """Build the AI's enclosure design in FreeCAD around the finished board; returns a summary."""
    spec = design.normalise(spec)
    if pcb is None:
        pcb = Path((p.state.get("artifacts") or {}).get("pcb") or "")
    if not pcb or not pcb.exists():
        p.step("enclosure", "error", "There is no finished board yet")
        return {"ok": False, "errors": ["There is no finished board yet. Get a clean build first."]}
    p.step("enclosure", "running", "Building the enclosure in FreeCAD")
    name = spec.get("name") or pcb.stem
    out_dir = pcb.parent / "enclosure"
    try:
        geo = geometry.read(pcb)
        s = enclosure.build(geo, spec, enc, out_dir, name, config.load(),
                            log=lambda m: p.log("enclosure", m), cancel=p.cancel)
    except Exception as e:  # noqa: BLE001
        p.step("enclosure", "error", str(e)[:200])
        return {"ok": False, "errors": [str(e)]}
    if s.get("errors"):
        p.step("enclosure", "error", f"{len(s['errors'])} problems in the enclosure design")
        return s
    with p.lock:
        p.state["enclosure_spec"] = enc
        p.save()
    labels = {"base_stl": "base.stl", "lid_stl": "lid.stl", "base_step": "base.step", "lid_step": "lid.step",
              "freecad": "FreeCAD model"}
    files = [{"name": Path(s["files"][k]).name, "label": v, "url": p.file_url(Path(s["files"][k]))}
             for k, v in labels.items() if s["files"].get(k)]
    macro = out_dir / f"{name}-enclosure.FCMacro"
    if macro.exists():
        files.append({"name": macro.name, "label": "FreeCAD macro", "url": p.file_url(macro)})
    p.visual("enclosure", "enclosure", "3D-printable enclosure", summary=s, spec=enc,
             preview=p.file_url(Path(s["preview"])), files=files)
    for n in s["notes"] + s["freecad_notes"]:
        p.log("enclosure", n)
    o = s["outer_mm"]
    if s["collisions"]:
        p.step("enclosure", "warn", f"{len(s['collisions'])} collisions to fix")
    else:
        pr = s.get("print") or {}
        p.step("enclosure", "done", f"{o[0]:g} × {o[1]:g} × {o[2]:g} mm, {s['filament_g']:g} g"
               + (f", ~{pr['hours']:.1f} h to print" if pr else ""))
    return {k: s[k] for k in ("ok", "collisions", "outer_mm", "inner_mm", "board_standoff_mm",
                              "board_top_to_lid_mm", "tallest_part", "board_fixing", "lid", "openings",
                              "lid_holes", "extras", "notes", "freecad_notes", "warnings", "filament_g", "print")
            if k in s}


def make_report(p: Project) -> Path | None:
    """The PDF project report, shown with the outputs."""
    from . import report
    try:
        pdf = report.generate(p)
    except Exception as e:  # noqa: BLE001
        p.log("outputs", f"Project report not written: {e}")
        return None
    p.visual("outputs", "report", "Project report (PDF)", url=p.file_url(pdf), name=pdf.name,
             made=time.strftime("%Y-%m-%d %H:%M"))
    return pdf


def run_case(p: Project, spec: dict, pcb: Path | None = None) -> dict:
    """Check the board fits the user's own box and draw the drilling templates."""
    spec = design.normalise(spec)
    pcb = pcb or Path((p.state.get("artifacts") or {}).get("pcb") or "")
    if not pcb.exists():
        return {"ok": False, "problems": ["There is no finished board yet."]}
    p.step("enclosure", "running", f"Fitting the board into the {spec['board']['case'].get('name') or 'case'}")
    try:
        r = stock_case.build(spec, geometry.read(pcb), pcb.parent / "case", spec["name"],
                             (p.state.get("enclosure_spec") or {}).get("part_heights"))
    except Exception as e:  # noqa: BLE001
        p.step("enclosure", "error", str(e)[:200])
        return {"ok": False, "problems": [str(e)]}
    files = [{"name": f["path"].name, "label": f["title"], "url": p.file_url(f["path"])} for f in r["files"]]
    p.visual("enclosure", "case", f"Fitting into a {r['case']['name']}", result={k: v for k, v in r.items()
             if k not in ("files", "page")}, files=files, page=p.file_url(r["page"]))
    for w in r["problems"] + r["warnings"]:
        p.log("enclosure", w)
    n = len(r["cutouts"])
    p.step("enclosure", "error" if r["problems"] else ("warn" if r["warnings"] else "done"),
           f"{len(r['problems'])} problems fitting the case" if r["problems"] else
           f"Fits, {r['headroom_mm']:g} mm headroom, {n} opening{'s' if n != 1 else ''} to drill")
    return {k: r[k] for k in ("ok", "case", "problems", "warnings", "cutouts", "headroom_mm")}


def run_quote(p: Project, spec: dict, opts: dict | None = None) -> dict:
    opts = {**config.load()["jlcpcb"], **(opts or {})}
    p.step("quote", "running", "Pricing at JLCPCB")
    try:
        q = jquote.full_quote(spec, opts, log=lambda s: p.log("quote", s))
    except Exception as e:  # noqa: BLE001
        p.step("quote", "error", str(e))
        return {"error": str(e)}
    if q.get("components"):
        try:
            q["savings"] = jsavings.find(q, spec, log=lambda s: p.log("quote", s))
        except Exception as e:  # noqa: BLE001
            p.log("quote", f"Cheaper-part search failed: {e}")
    try:
        q["fabs"] = jfabs.compare(design.normalise(spec), q)
    except Exception as e:  # noqa: BLE001
        p.log("quote", f"Other fabs not compared: {e}")
    p.state["quote"] = q
    p.save()
    rev = p.state.get("revision", 0)
    (p.root / f"rev{rev}" / "quote.json").write_text(json.dumps(q, indent=1))
    summary = p.root / f"rev{rev}" / "summary.json"
    if summary.exists():
        try:
            sm = json.loads(summary.read_text())
            sm["total"], sm["per_board"] = q.get("total"), q.get("per_board")
            summary.write_text(json.dumps(sm, indent=1))
        except (OSError, ValueError):
            pass
    p.visual("quote", "quote", "JLCPCB quote", quote=q, options=opts)
    sv = q.get("savings") or {}
    p.step("quote", "done", f"${q['total']:.2f} total" + (f", ${sv['total_saving']:.2f} to save" if sv.get("total_saving") else ""))
    short = [l for l in (q.get("components") or {}).get("lines", []) if l["status"] != "ok"]
    return {"total": q["total"], "per_board": q["per_board"],
            "pcb": q["pcb"]["subtotal"], "assembly": (q.get("assembly") or {}).get("subtotal"),
            "components": (q.get("components") or {}).get("subtotal"),
            "shipping": q["shipping"]["subtotal"],
            "part_problems": [{"refs": l["refs"], "lcsc": l["lcsc"], "status": l["status"]} for l in short],
            "extended_parts": [l["lcsc"] for l in (q.get("components") or {}).get("lines", [])
                               if l.get("library_type") == "extended"],
            "panel_suggestion": ({k: (q.get("panel_options") or {}).get("recommended", {}).get(k)
                                  for k in ("nx", "ny", "method", "boards", "pcb_cost")}
                                 | {"reason": q["panel_options"].get("reason")}
                                 if (q.get("panel_options") or {}).get("recommended") else None),
            "cheaper_parts": [{"refs": x["refs"], "use_lcsc": x["to"]["lcsc"], "mpn": x["to"]["mpn"],
                               "saving": x["saving"], "reason": x["reason"]} for x in sv.get("suggestions") or []]}


def build_firmware(p: Project) -> dict:
    """Compile the latest revision's starter firmware with PlatformIO and offer the result for flashing."""
    vis = (p.state.get("visuals") or {}).get("outputs:firmware")
    folder = (p.state.get("artifacts") or {}).get("folder")
    if not vis or not folder or not (Path(folder) / "firmware").is_dir():
        return {"ok": False, "error": "This board has no starter firmware."}
    fw = vis["firmware"]
    src = Path(folder) / "firmware"
    work = p.root / "firmware-build" / Path(folder).name     # outside the revision, so .pio stays out of its zip
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, work, dirs_exist_ok=True)
    data = {k: v for k, v in vis.items() if k != "title"}
    p.visual("outputs", "firmware", vis["title"], **{**data, "build": {"status": "running"}})
    try:
        res = fwbuild.build(work, fw["env"], fw["family"], log=lambda m: p.log("outputs", m))
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": str(e)}
    show = {k: res.get(k) for k in ("ok", "seconds", "usage", "error", "kind", "chip")}
    show["status"] = "done" if res.get("ok") else "failed"
    flash = work / "flash"
    flash.mkdir(exist_ok=True)
    show["files"] = []
    for f in res.get("files") or []:
        dest = flash / f["name"]
        shutil.copyfile(f["path"], dest)     # boot_app0.bin lives in PlatformIO's own folder
        show["files"].append({"name": f["name"], "url": p.file_url(dest), "offset": f.get("offset")})
    p.visual("outputs", "firmware", vis["title"], **{**data, "build": show})
    p.log("outputs", f"Firmware compiled in {res.get('seconds')} s" if res.get("ok")
          else f"Firmware did not compile: {res.get('error')}")
    return show


def refresh_bom(p: Project, spec: dict) -> list[str]:
    """Rewrite the latest revision's BOM, CPL and project zip after part numbers change."""
    folder = (p.state.get("artifacts") or {}).get("folder")
    if not folder or not (Path(folder) / "fab").is_dir():
        return []
    rdir = Path(folder)
    fab = rdir / "fab"
    done = []
    for bom in fab.glob("*-bom.csv"):
        jquote.write_bom(jquote.bom_rows(spec), bom)
        done.append(bom.name)
    for pos in fab.glob("*-pos.csv"):
        excl = {c["ref"] for c in spec["components"] if not c.get("lcsc")}
        overrides = {c["ref"]: c["cpl"] for c in spec["components"] if isinstance(c.get("cpl"), dict)}
        cpl, _ = cli.jlc_cpl(pos, fab / pos.name.replace("-pos.csv", "-cpl.csv"), excl, overrides)
        done.append(cpl.name)
    for z in (p.root / "exports").glob(f"*-rev{p.state.get('revision', 0)}-kicad-project.zip"):
        shutil.make_archive(str(z.with_suffix("")), "zip", rdir)
        done.append(z.name)
    return done


def _off_board(geo: dict) -> list[str]:
    bb = geo.get("bbox")
    if not bb:
        return []
    x0, y0, x1, y1 = bb
    out = []
    for f in geo["footprints"]:
        if not (x0 - 0.5 <= f["x"] <= x1 + 0.5 and y0 - 0.5 <= f["y"] <= y1 + 0.5):
            out.append(f["ref"])
    return out
