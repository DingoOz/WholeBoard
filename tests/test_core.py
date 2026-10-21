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
    assert text.count("(global_label") >= 20


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
