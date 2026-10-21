"""A PDF report of the project: summary, renders, cost, BOM, simulation, power and enclosure, then the schematic."""

from __future__ import annotations

import io
import re
import time
from pathlib import Path

from . import design
from .jlc import quote as jquote
from .project import Project


def _path(p: Project, url: str | None) -> Path | None:
    if not url:
        return None
    m = re.search(r"/files/([^?]+)", url)
    f = p.root / m[1] if m else None
    return f if f and f.exists() else None


def markdown(text: str, style, esc) -> list:
    """Paragraphs from the AI's light markdown: headings, bullets, **bold** and `code`."""
    from reportlab.platypus import Paragraph
    out = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        t = esc(re.sub(r"^#+\s*", "", line))
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", t)
        if line.startswith("#"):
            t = f"<b>{t}</b>"
        elif re.match(r"^[-*] ", line):
            t = "• " + t[2:]
        out.append(Paragraph(t, style))
    return out


def generate(p: Project) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    st = p.state
    spec = design.normalise(st.get("spec") or {})
    vis = st.get("visuals") or {}
    folder = Path((st.get("artifacts") or {}).get("folder") or p.root)
    rev = st.get("revision", 0)
    out_dir = p.root / "exports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{spec['name']}-rev{rev}-report.pdf"

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555"))
    h1, h2 = ss["Title"], ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, spaceBefore=8, spaceAfter=4)
    esc = lambda s: str(s if s is not None else "–").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731

    def table(head, rows, widths=None):
        data = [[Paragraph(f"<b>{esc(h)}</b>", small) for h in head]] + \
               [[Paragraph(esc(c), small) for c in r] for r in rows]
        t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.2, colors.HexColor("#cccccc")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        return t

    def img(path: Path, w_mm: float):
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(str(path)).getSize()
        return Image(str(path), width=w_mm * mm, height=w_mm * mm * ih / iw)

    story: list = []
    b = spec["board"]
    story += [Paragraph(esc(st.get("title") or spec["name"]), h1),
              Paragraph(f"{esc(spec['name'])} · revision {rev} · {time.strftime('%Y-%m-%d')} · made with WholeBoard", small),
              Spacer(1, 4 * mm)]
    if spec.get("summary"):
        story.append(Paragraph(esc(spec["summary"]), body))
    renders = [f for f in (folder / "render-iso.png", folder / "render-top.png", folder / "render-bottom.png") if f.exists()]
    if renders:
        story += [Spacer(1, 3 * mm), img(renders[0], 95)]
        if len(renders) > 1:
            story.append(Table([[img(f, 70) for f in renders[1:3]]], hAlign="LEFT"))

    drc = (vis.get("drc:drc") or {}).get("report") or {}
    facts = [["Board", f"{b['width_mm']:g} × {b['height_mm']:g} mm, {b['layers']} layers, {b.get('thickness_mm', 1.6):g} mm"
              + (", custom outline" if b.get("outline") else "")],
             ["Parts / nets", f"{len(spec['components'])} parts, {len(spec['nets'])} nets"],
             ["Design rule check", f"{drc.get('errors', '–')} errors, {drc.get('unconnected', '–')} unconnected, "
                                   f"{drc.get('warnings', '–')} warnings" if drc else "not run"]]
    if spec.get("blocks_used"):
        facts.append(["Building blocks", ", ".join(f"{x['name']} ({x['block']})" for x in spec["blocks_used"])])
    u = st.get("usage")
    if u:
        facts.append(["AI", f"{u.get('model')}: {u.get('turns')} turns, {u.get('input', 0) + u.get('cache_read', 0):,} in / "
                            f"{u.get('output', 0):,} out tokens, ${u.get('cost', 0):.2f}"])
    story += [Paragraph("Overview", h2), table(["", ""], facts, [35 * mm, 135 * mm])]

    q = st.get("quote") or {}
    if q.get("total") is not None:
        rows = [["PCB", f"${q['pcb']['subtotal']:.2f}"]]
        if q.get("assembly"):
            rows.append(["Assembly", f"${q['assembly']['subtotal']:.2f}"])
        if q.get("components"):
            rows.append(["Components", f"${q['components']['subtotal']:.2f}"])
        rows += [["Shipping", f"${q['shipping']['subtotal']:.2f}"],
                 ["Total", f"${q['total']:.2f} for {q.get('quantity')} boards (${q['per_board']:.2f} each)"]]
        story += [Paragraph("JLCPCB quote", h2), table(["Item", "Cost (US$)"], rows, [35 * mm, 135 * mm])]
        if q.get("fabs"):
            story += [Spacer(1, 2 * mm), table(["Fab", "Boards", "Bare boards", "Shipping", "Lead time", "Notes"],
                      [[f["fab"], f.get("boards"), f"${f['total']:.2f}" if f.get("total") is not None else "quote online",
                        f.get("shipping", "–"), f.get("lead_time", "–"), f.get("notes", "")] for f in q["fabs"]],
                      [22 * mm, 14 * mm, 22 * mm, 26 * mm, 26 * mm, 60 * mm])]

    if st.get("final_summary"):
        story += [PageBreak(), Paragraph("The designer's summary", h2)] + markdown(st["final_summary"], body, esc)

    story.append(PageBreak())
    story.append(Paragraph("Bill of materials", h2))
    lines = {tuple(l["refs"]): l for l in (q.get("components") or {}).get("lines") or []}
    rows = []
    for r in jquote.bom_rows(spec):
        l = lines.get(tuple(r["refs"]), {})
        rows.append([", ".join(r["refs"]), len(r["refs"]), r["value"], r["footprint"].split(":")[-1], r["lcsc"] or "–",
                     l.get("library_type", "–"), f"${l['total']:.2f}" if l.get("total") else "–"])
    story.append(table(["Refs", "Qty", "Value", "Footprint", "LCSC", "Type", "Cost"], rows,
                       [34 * mm, 8 * mm, 26 * mm, 46 * mm, 20 * mm, 18 * mm, 14 * mm]))

    sim = (vis.get("sim:sim") or {}).get("result") or {}
    power = (vis.get("sim:power") or {}).get("budget") or {}
    if sim.get("ran"):
        story += [PageBreak(), Paragraph("Simulation (ngspice)", h2)]
        if sim.get("rails"):
            story.append(table(["Rail", "Voltage", "At peak load", "Ripple"],
                               [[r["net"], f"{r['volts']:.3f} V", f"{r['peak_load']:.3f} V", f"{r['ripple_v'] * 1000:.0f} mV"]
                                for r in sim["rails"]]))
        if power.get("supplies"):
            story += [Paragraph("Power budget", h2), table(
                ["Supply", "Typical", "Peak", "Limit", "Use at peak"],
                [[s["net"], f"{s['current_ma']:.3g} mA", f"{s['peak_ma']:.3g} mA",
                  f"{s['max_ma']:g} mA" if s.get("max_ma") else "–", f"{s['use_pct']:.0f} %" if s.get("use_pct") else "–"]
                 for s in power["supplies"]])]
        if power.get("regulators"):
            story += [Spacer(1, 2 * mm), table(["Regulator", "Load (typ / peak)", "Rating", "Heat", "Temperature"],
                      [[f"{r['ref']} {r.get('value') or ''}", f"{r['current_ma']:.3g} / {r.get('peak_ma') or 0:.3g} mA",
                        f"{r['max_ma']:g} mA", f"{r['power_mw']:.0f} mW" if r.get("power_mw") else "–",
                        f"{r['temp_c']:.0f} °C" if r.get("temp_c") else "–"] for r in power["regulators"]])]
        for bt in power.get("battery") or []:
            txt = f"Battery on {bt['net']} ({bt['capacity_mah']} mAh): {bt['hours_typical']} h typical, {bt['hours_peak']} h at peak"
            if bt.get("profile"):
                txt += f", {bt['profile']['hours']} h with {bt['profile']['active_pct']:g} % active"
            story.append(Paragraph(esc(txt), body))
        notes = (sim.get("problems") or []) + (sim.get("warnings") or []) + (power.get("notes") or [])
        if notes:
            story += [Paragraph("Notes", h2)] + [Paragraph("• " + esc(n), body) for n in notes[:20]]
    checks = (vis.get("drc:checks") or {}).get("report") or {}
    if checks.get("problems") or checks.get("warnings"):
        story += [Paragraph("Board checks", h2)] + [Paragraph("• " + esc(n), body)
                                                    for n in checks["problems"] + checks["warnings"]]

    enc = (vis.get("enclosure:enclosure") or {}).get("summary")
    case = (vis.get("enclosure:case") or {}).get("result")
    if enc or case:
        part = [Paragraph("Enclosure", h2)]
        if enc:
            o = enc["outer_mm"]
            pr = enc.get("print") or {}
            part.append(Paragraph(esc(f"3D-printed, {o[0]:g} × {o[1]:g} × {o[2]:g} mm, {enc.get('filament_g', 0):g} g"
                                      + (f", about {pr['hours']:.1f} h to print, {pr.get('currency', '$')}{pr.get('total', 0):.2f}" if pr else "")
                                      + f". {len(enc.get('openings') or [])} openings, {len(enc.get('lid_holes') or [])} lid holes,"
                                      f" {len(enc.get('collisions') or [])} collisions."), body))
        if case:
            part.append(Paragraph(esc(f"Fitted into a {case['case']['name']}: {case['headroom_mm']:g} mm above the tallest "
                                      f"part, {len(case['cutouts'])} openings to drill."), body))
            for n in case["problems"] + case["warnings"]:
                part.append(Paragraph("• " + esc(n), body))
        story.append(KeepTogether(part))

    bu = vis.get("outputs:bringup")
    if bu:
        story += [PageBreak(), Paragraph("Bring-up checklist", h2)]
        for i, s in enumerate(bu["steps"], 1):
            story.append(Paragraph(f"<b>{i}. {esc(s['title'])}</b>", body))
            story += [Paragraph("☐ " + esc(x), small) for x in s["items"]]
            if s.get("table") and s["table"]["rows"]:
                story += [Spacer(1, 1 * mm), table(s["table"]["head"], s["table"]["rows"]), Spacer(1, 2 * mm)]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"{spec['name']} rev {rev}", author="WholeBoard")
    doc.build(story)
    pdf = buf.getvalue()

    sch = next(iter(folder.glob("*.kicad_sch")), None)
    if sch:
        try:
            from pypdf import PdfReader, PdfWriter

            from .kicad import cli
            sch_pdf = folder / f"{spec['name']}-schematic.pdf"
            cli.run(["sch", "export", "pdf", "-o", str(sch_pdf), str(sch)], cwd=folder)
            w = PdfWriter()
            for src in (io.BytesIO(pdf), sch_pdf):
                for page in PdfReader(src).pages:
                    w.add_page(page)
            buf = io.BytesIO()
            w.write(buf)
            pdf = buf.getvalue()
        except Exception as e:  # noqa: BLE001 - the report is still useful without the schematic
            p.log("outputs", f"Schematic not added to the report: {e}")
    out.write_bytes(pdf)
    return out
