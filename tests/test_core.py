import json
from pathlib import Path

import pytest

from wholeboard import design, sexpr
from wholeboard.jlc import parts, quote
from wholeboard.kicad import board, geometry, libs, schematic

SPEC = json.loads((Path(__file__).parent / "sample_spec.json").read_text())
HAVE_LIBS = libs.locate("footprints") is not None


def test_sexpr_roundtrip():
    text = '(kicad_pcb (version 20260206) (net "A \\"q\\"") (at 1.5 -2))'
    node = sexpr.parse(text)
    assert sexpr.parse(sexpr.dumps(node)) == node
    assert node[2][1] == 'A "q"'


def test_parse_value():
    assert parts.parse_value("4k7") == 4700
    assert parts.parse_value("100nF") == pytest.approx(1e-7)
    assert parts.parse_value("0R") == 0


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_validate_catches_bad_pad():
    bad = json.loads(json.dumps(SPEC))
    bad["nets"][0]["pins"].append("U1.9")
    rep = design.validate(bad)
    assert not rep.ok and any("no pad '9'" in e for e in rep.errors)
    assert design.validate(SPEC).ok


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_board_and_schematic(tmp_path):
    out = board.build(SPEC, tmp_path)
    geo = geometry.read(Path(out["pcb"]))
    assert len(geo["footprints"]) == len(SPEC["components"])
    assert geo["ratsnest"]
    nets = {p["net"] for p in geo["pads"] if p["net"]}
    assert {"GND", "VBUS", "+3V3"} <= nets
    assert board.add_ground_pour(Path(out["pcb"]), SPEC)
    sch = schematic.build(SPEC, tmp_path)
    text = Path(sch["sch"]).read_text()
    assert text.count("(global_label") + text.count("(label") >= 20      # supply nets global, the rest local
    assert sch["pages"][0]["title"] == "Main" and len(sch["pages"]) == 1


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_schematic_sheets_and_signal_order(tmp_path):
    spec = json.loads(json.dumps(SPEC))
    reg = [c["ref"] for c in spec["components"] if schematic.stage(c) == 2]
    assert reg, "the sample has a regulator"
    for c in spec["components"]:
        if c["ref"] in reg or (c["ref"].startswith("C") and c["ref"] in ("C1", "C2")):
            c["sheet"] = "Power"
    sch = schematic.build(spec, tmp_path)
    assert [p["title"] for p in sch["pages"]] == ["Main", "Power"]
    sub_ = (tmp_path / sch["pages"][1]["file"]).read_text()
    root = Path(sch["sch"]).read_text()
    assert "(sheet" in root and '"Sheetfile"' in root and "(sheet_instances" in root
    assert "(global_label" in sub_ and "(sheet_instances" not in sub_
    links = schematic.board_links(spec)
    assert links[reg[0]][0].count("/") == 2 and links[reg[0]][1] == "/Power/"
    assert links["J1"][1] == "/"
    assert schematic.stage({"ref": "J1"}) < schematic.stage({"ref": "F1"}) < schematic.stage(
        {"ref": "U1", "value": "AMS1117-3.3"}) < schematic.stage({"ref": "U2", "value": "ESP32"}) < schematic.stage({"ref": "D1"})


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_bom_groups():
    rows = quote.bom_rows(SPEC)
    r = next(r for r in rows if r["lcsc"] == "C23186")
    assert sorted(r["refs"]) == ["R1", "R2"]


def test_pcb_price_promo_and_area():
    P = quote.pricing()
    _, small = quote.pcb_price(50, 50, 2, 5, {}, P)
    _, big = quote.pcb_price(150, 150, 2, 50, {}, P)
    assert small == P["pcb"]["promo"]["2"]["price"]
    assert big > small


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_add_ground_pour_fills_only_layers_the_router_did_not_pour(tmp_path):
    out = board.build(SPEC, tmp_path)
    pcb = Path(out["pcb"])
    assert board.ground_pours(SPEC) == ["GND@F.Cu", "GND@B.Cu"]
    text = pcb.read_text()      # as `tracemaker route --pour GND@B.Cu` leaves it
    pcb.write_text(text.rstrip()[:-1] + '(zone (net 1) (net_name "GND") (layer "B.Cu") (name "tracemaker pour")'
                   ' (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))))\n')
    assert board.add_ground_pour(pcb, SPEC)
    zones = [(str(sexpr.find(z, "name")[1]), str(sexpr.find(z, "layer")[1]))
             for z in sexpr.parse(pcb.read_text()) if sexpr.tag(z) == "zone"]
    assert sorted(zones) == [("GND_F_Cu", "F.Cu"), ("tracemaker pour", "B.Cu")]
    assert board.drop_pours(pcb) == 1 and board.drop_pours(pcb) == 0


def test_no_pour_entries_when_the_pour_is_turned_off():
    spec = json.loads(json.dumps(SPEC))
    spec["board"]["ground_pour"] = None
    assert board.ground_pours(spec) == []
    spec["board"]["ground_pour"] = "NO_SUCH_NET"
    assert board.ground_pours(spec) == []
