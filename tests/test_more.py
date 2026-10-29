"""Building blocks, power budget, impedance, stock check, test pads, bring-up, assembly page, DXF outlines,
stock cases, other fabs, firmware builds, the expert model and the PDF report."""

import json
from pathlib import Path

import pytest

from wholeboard import blocks, bringup, case, design, dxf, fwbuild, ibom, impedance, power, sexpr
from wholeboard.jlc import fabs, stock
from wholeboard.kicad import libs

HAVE_LIBS = bool(libs.search_footprints("R_0603", 1))
needs_libs = pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")


# ------------------------------------------------------------------ blocks
@needs_libs
@pytest.mark.parametrize("bid", [b["id"] for b in blocks.BUILTIN])
def test_every_block_is_valid(bid):
    b = blocks.catalogue()[bid]
    entry = {"block": bid, "name": "B",
             "connect": {k: f"N_{k}" for k, v in b["ports"].items() if v.get("required")}}
    spec = design.normalise({"name": "t", "board": {"width_mm": 120, "height_mm": 120}, "blocks": [entry]})
    rep = design.validate(spec)
    assert rep.errors == []
    assert not [w for w in rep.warnings if "not connected" in w]


@needs_libs
def test_blocks_renumber_and_merge_nets():
    spec = design.normalise({"name": "t", "board": {"width_mm": 80, "height_mm": 60},
                             "components": [{"ref": "R1", "value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric",
                                             "lcsc": "C25804"}],
                             "nets": [{"name": "+3V3", "class": "power", "pins": ["R1.1"]},
                                      {"name": "LED", "pins": ["R1.2"]}],
                             "blocks": [{"block": "led_indicator", "name": "L1", "connect": {"IN": "LED"}},
                                        {"block": "led_indicator", "name": "L2", "connect": {"IN": "+3V3"}}]})
    refs = [c["ref"] for c in spec["components"]]
    assert refs == ["R1", "R2", "D1", "R3", "D2"]
    nets = {n["name"]: n for n in spec["nets"]}
    assert "R2.1" in nets["LED"]["pins"] and "R1.2" in nets["LED"]["pins"]
    assert nets["+3V3"]["class"] == "power" and "R3.1" in nets["+3V3"]["pins"]
    assert "L1_LED_A" in nets and "L2_LED_A" in nets
    assert spec["blocks_used"][0]["parts"] == {"R1": "R2", "D1": "D1"}
    assert "blocks" not in spec
    assert design.normalise(spec)["components"] == spec["components"]     # expanding twice changes nothing


@needs_libs
def test_block_options_overrides_and_errors():
    spec = design.normalise({"name": "t", "board": {"width_mm": 80, "height_mm": 60}, "blocks": [
        {"block": "qwiic_i2c", "name": "Q", "options": {"pullups": False}},
        {"block": "led_indicator", "name": "G", "connect": {"IN": "X"}, "parts": {"D1": {"lcsc": "C2297", "value": "Green"}}},
        {"block": "can_sn65hvd230", "name": "CAN"},
        {"block": "nope"}]})
    assert not [c for c in spec["components"] if c.get("block") == "Q" and c["block_ref"].startswith("R")]
    assert next(c for c in spec["components"] if c.get("block") == "G" and c["block_ref"] == "D1")["lcsc"] == "C2297"
    errs = " ".join(spec["block_errors"])
    assert "port TXD must be connected" in errs and "unknown block 'nope'" in errs
    assert design.validate(spec).errors


@needs_libs
def test_usb_block_adds_impedance_and_supply():
    spec = design.normalise({"name": "t", "board": {"width_mm": 60, "height_mm": 40, "layers": 4},
                             "blocks": [{"block": "usb_c_data", "name": "USB"}]})
    assert spec["impedance"][0]["nets"] == ["USB_DP", "USB_DN"]
    assert spec["simulation"]["sources"][0]["net"] == "VBUS"
    assert impedance.has_pairs(spec)


def test_user_block_file(tmp_path, monkeypatch):
    monkeypatch.setattr(blocks, "USER_DIR", tmp_path)
    (tmp_path / "mine.json").write_text(json.dumps({"id": "my_block", "title": "Mine", "components": [], "ports": {}}))
    assert blocks.catalogue()["my_block"]["user"]
    assert any(b["id"] == "my_block" for b in blocks.listing())


# ------------------------------------------------------------------ power budget
SIM_RES = {
    "ran": True, "ambient_c": 25,
    "supplies": [{"net": "VBUS", "kind": "usb", "volts": 5, "current_ma": 80, "peak_ma": 420, "max_ma": 500, "power_mw": 400},
                 {"net": "VBAT", "kind": "battery", "volts": 3.7, "current_ma": 50, "peak_ma": 300, "battery_hours": 20}],
    "rails": [{"net": "+3V3", "volts": 3.3, "op": 3.3, "peak_load": 3.25, "ripple_v": 0.01}],
    "parts": [{"ref": "U1", "kind": "ldo", "value": "AMS1117-3.3", "vin": 5, "vout": 3.3, "current_ma": 80, "peak_ma": 420,
               "max_ma": 800, "power_mw": 900, "temp_c": 150},
              {"ref": "D1", "kind": "led", "value": "Red", "current_ma": 2.0}],
    "loads": [{"ref": "U2", "nets": ["+3V3"], "typ_ma": 80, "peak_ma": 400}],
}


def test_power_budget():
    spec = {"name": "t", "components": [{"ref": "U1", "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2"}], "nets": [],
            "simulation": {"sources": [{"net": "VBAT", "capacity_mah": 1000}], "battery_profile": {"active_pct": 10, "sleep_ma": 0.1}}}
    b = power.budget(spec, SIM_RES)
    assert b["supplies"][0]["use_pct"] == 84.0
    reg = b["regulators"][0]
    assert reg["use_pct"] == 52.5 and reg["theta_needed_c_w"] == 83
    assert b["rails"][0]["loads"][0]["ref"] == "U2"
    bat = b["battery"][0]
    assert bat["hours_typical"] == 20.0 and bat["profile"]["avg_ma"] == pytest.approx(5.09)
    assert b["thermal"][0]["ref"] == "U1" and b["thermal"][0]["margin_c"] < 0
    assert any("84%" in n for n in b["notes"]) and any("°C/W" in n for n in b["notes"])
    assert power.budget(spec, {"ran": False})["supplies"] == []


# ------------------------------------------------------------------ impedance
def test_impedance_widths():
    four = impedance.design_rule({"nets": ["X"], "ohms": 50}, 4, 1.6, 0.2)
    assert four["practical"] and 0.3 < four["width_mm"] < 0.4
    diff = impedance.design_rule({"nets": ["A_P", "A_N"], "kind": "diff", "ohms": 90}, 4, 1.6, 0.2)
    assert diff["practical"] and abs(diff["achieved"] - 90) < 1
    two = impedance.design_rule({"nets": ["A_P", "A_N"], "kind": "diff", "ohms": 90}, 2, 1.6, 0.2)
    assert not two["practical"] and "USB full speed" in two["note"]
    assert impedance.pair_names("USB_DP", "USB_DN") and impedance.pair_names("D+", "D-")
    assert not impedance.pair_names("CANH", "CANL")


def test_impedance_check_after_routing():
    spec = design.normalise({"name": "t", "board": {"layers": 4}, "components": [],
                             "nets": [{"name": "USB_DP", "pins": []}, {"name": "USB_DN", "pins": []}],
                             "impedance": [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff", "max_skew_mm": 1}]})
    w = impedance.rules(spec)[0]["width_mm"]
    geo = {"tracks": [{"a": [0, 0], "b": [10, 0], "w": w, "net": "USB_DP", "layer": "F.Cu"},
                      {"a": [0, 1], "b": [13, 1], "w": w, "net": "USB_DN", "layer": "F.Cu"}],
           "arcs": [], "vias": [{"net": "USB_DN"}]}
    r = impedance.check(spec, geo)
    assert r["groups"][0]["skew_mm"] == 3.0
    assert any("differ in length" in x for x in r["warnings"]) and any("vias" in x for x in r["warnings"])
    assert "JLC04161H-7628" in r["order_note"]
    errs, warns = impedance.validate({"nets": [{"name": "CANH"}, {"name": "CANL"}],
                                      "impedance": [{"nets": ["CANH", "CANL"], "kind": "diff"}, {"nets": ["Q"]}]})
    assert errs == ["impedance[1]: there is no net Q"] and "not named as a pair" in warns[0]


@needs_libs
def test_impedance_net_classes_in_project():
    from wholeboard.kicad import board
    spec = design.normalise({"name": "t", "board": {"layers": 4}, "components": [],
                             "nets": [{"name": "USB_DP", "class": "power", "pins": []}, {"name": "USB_DN", "pins": []}],
                             "impedance": [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff"}]})
    ns = board.project_json(spec)["net_settings"]
    cls = next(c for c in ns["classes"] if c["name"].startswith("Impedance1"))
    assert cls["diff_pair_gap"] == 0.2 and 0.25 < cls["diff_pair_width"] < 0.35
    assert {"netclass": cls["name"], "pattern": "USB_DP"} in ns["netclass_patterns"]
    assert {"netclass": "Power", "pattern": "USB_DP"} not in ns["netclass_patterns"]


# ------------------------------------------------------------------ live stock
def test_stock_check(monkeypatch):
    parts = {"C1": {"lcsc": "C1", "mpn": "A", "stock": 1_000_000, "library_type": "basic", "prices": [], "min_order": 1},
             "C2": {"lcsc": "C2", "mpn": "B", "stock": 3, "library_type": "extended", "prices": [], "min_order": 1,
                    "restock_date": "2026-11-30"},
             "C3": {"lcsc": "C3", "mpn": "C", "stock": 50, "library_type": "basic", "prices": [], "min_order": 1},
             "C4": {"lcsc": "C4", "mpn": "D", "stock": 900, "library_type": "basic", "prices": [], "min_order": 1,
                    "buyable": False, "no_buy_reason": "discontinued"}}
    monkeypatch.setattr(stock.jparts, "lookup", lambda code, fresh=False: parts.get(code))
    monkeypatch.setattr(stock.savings, "find", lambda q, s, log: {"suggestions": [{"refs": ["U2"], "to": {"lcsc": "C9"}}]})
    rows = [{"lcsc": c, "value": c, "footprint": "Lib:X", "refs": [r]} for c, r in
            (("C1", "R1"), ("C2", "U2"), ("C3", "U3"), ("C4", "U4"), ("C5", "U5"), ("", "J1"))]
    monkeypatch.setattr(stock, "bom_rows", lambda spec: rows)
    r = stock.check({}, {"assembly_quantity": 5})
    st = {l["refs"][0]: l["status"] for l in r["lines"]}
    assert st == {"R1": "ok", "U2": "short", "U3": "low", "U4": "unavailable", "U5": "not found", "J1": "no LCSC number"}
    assert "restock expected 2026-11-30" in r["lines"][1]["note"]
    assert r["problems"] == 3 and r["low"] == 1 and not r["ok"] and r["replacements"]


# ------------------------------------------------------------------ test pads and bring-up
@needs_libs
def test_test_points_on_rails():
    spec = design.normalise({"name": "t", "board": {"width_mm": 40, "height_mm": 30, "test_points": True},
                             "components": [{"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric", "lcsc": "C21190"}],
                             "nets": [{"name": "VIN", "class": "power", "pins": ["R1.1"]},
                                      {"name": "GND", "class": "power", "pins": ["R1.2"]}]})
    tps = [c for c in spec["components"] if c.get("test_point_for")]
    assert [(c["ref"], c["test_point_for"]) for c in tps] == [("TP1", "VIN"), ("TP2", "GND")]
    assert len(design.normalise(spec)["components"]) == 3          # not added twice
    rep = design.validate(spec)
    assert rep.ok and not [w for w in rep.warnings if "TP" in w]
    from wholeboard.jlc.quote import bom_rows
    assert all(not r["footprint"].startswith("TestPoint") for r in bom_rows(spec))


def test_bringup_guide():
    spec = {"name": "t", "components": [{"ref": "TP1", "footprint": "TestPoint:TestPoint_Pad_D1.5mm", "test_point_for": "+3V3"},
                                        {"ref": "U1", "footprint": "Package_DFN_QFN:QFN-56-1EP_7x7mm"},
                                        {"ref": "D1", "footprint": "LED_SMD:LED_0603", "symbol": "Device:LED"}],
            "nets": [{"name": "+3V3", "class": "power", "pins": ["TP1.1"]}, {"name": "GND", "pins": []},
                     {"name": "SDA", "pins": []}]}
    res = {"rails": [{"net": "+3V3", "volts": 3.3}],
           "supplies": [{"net": "VBUS", "kind": "usb", "volts": 5, "current_ma": 40, "peak_ma": 300}],
           "signals": [{"net": "CLK", "frequency_hz": 1000, "duty": 0.5}],
           "parts": [{"ref": "D1", "kind": "led", "current_ma": 2}]}
    g = bringup.guide(spec, res, {"thermal": [{"ref": "U1", "value": "x", "temp_c": 60, "power_mw": 300}]},
                      {"mcu": "U1", "family": "RP2040", "pins": [{"macro": "PIN_LED", "net": "LED", "role": "led"}]})
    titles = [s["title"] for s in g["steps"]]
    assert titles[0] == "Look before powering" and "Program it" in titles
    md = g["markdown"]
    assert "test point TP1" in md and "BOOTSEL" in md and "about 70 mA" in md and "U1" in md and "SDA" in md
    assert "3.13 – 3.46 V" in md


# ------------------------------------------------------------------ assembly page
@needs_libs
def test_assembly_page(tmp_path):
    spec = {"name": "t", "components": [
        {"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric", "lcsc": "C21190"},
        {"ref": "J1", "value": "Hdr", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"}], "nets": []}
    geo = {"outline": [{"kind": "line", "start": [0, 0], "end": [10, 0]}], "bbox": [0, 0, 10, 10],
           "footprints": [{"ref": "R1", "x": 2, "y": 2, "rot": 0, "side": "F.Cu", "court": [1, 1, 3, 3]},
                          {"ref": "J1", "x": 6, "y": 6, "rot": 0, "side": "F.Cu", "court": [5, 2, 7, 9]}],
           "pads": [{"ref": "R1", "x": 2, "y": 2, "w": 1, "h": 1, "rot": 0, "shape": "rect", "kind": "smd", "drill": 0,
                     "front": True, "back": False}]}
    out = ibom.generate(spec, geo, tmp_path / "a.html")
    text = out.read_text()
    data = json.loads(text.split("const D = ", 1)[1].split(";\n", 1)[0])
    fits = {g["refs"][0]: g["fit"] for g in data["bom"]}
    assert fits == {"J1": "by hand", "R1": "JLCPCB"}
    assert data["bom"][0]["tht"] is True and "</script>" in text


# ------------------------------------------------------------------ DXF outlines and shaped boards
DXF = """0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n1\n0\nENDSEC\n0\nSECTION\n2\nENTITIES
0\nLINE\n8\n0\n10\n0\n20\n0\n11\n2\n21\n0
0\nLINE\n8\n0\n10\n2\n20\n0\n11\n2\n21\n1
0\nARC\n8\n0\n10\n1.5\n20\n1\n40\n0.5\n50\n0\n51\n180
0\nLINE\n8\n0\n10\n1\n20\n1\n11\n0\n21\n1
0\nLINE\n8\n0\n10\n0\n20\n1\n11\n0\n21\n0
0\nCIRCLE\n8\n0\n10\n0.25\n20\n0.25\n40\n0.0625\n0\nENDSEC\n0\nEOF\n"""


def test_dxf_outline_in_inches():
    r = dxf.read(DXF)
    assert r["width_mm"] == pytest.approx(50.8) and r["height_mm"] == pytest.approx(38.1)
    assert not r["rectangular"] and len(r["outline"]) > 6
    assert r["holes"] == [{"x_mm": 6.35, "y_mm": 31.75, "diameter_mm": 3.17}]     # y measured down from the top
    assert any("inches" in n for n in r["notes"])
    with pytest.raises(ValueError):
        dxf.read("0\nSECTION\n2\nENTITIES\n0\nLINE\n10\n0\n20\n0\n11\n1\n21\n0\n0\nENDSEC\n0\nEOF\n")


@needs_libs
def test_shaped_board_and_hole_positions(tmp_path):
    from wholeboard.kicad import board
    L = [[0, 0], [40, 0], [40, 20], [20, 30], [0, 30]]
    base = {"name": "t", "board": {"outline": L, "mounting_holes": {"diameter_mm": 3.2, "positions": [[5, 5], [35, 5]]}},
            "components": [{"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric", "lcsc": "C21190",
                            "placement": {"x_mm": 10, "y_mm": 10}}],
            "nets": []}
    spec = design.normalise(base)
    assert (spec["board"]["width_mm"], spec["board"]["height_mm"]) == (40, 30)
    assert design.validate(spec).ok
    bad = json.loads(json.dumps(base))
    bad["components"][0]["placement"] = {"x_mm": 38, "y_mm": 28}
    bad["board"]["mounting_holes"]["positions"].append([39, 29])
    errs = " ".join(design.validate(bad).errors)
    assert "outside the board outline" in errs and "H3" in errs
    out = board.build(spec, tmp_path)
    text = Path(out["pcb"]).read_text()
    assert text.count("Edge.Cuts") >= 5 and text.count('"MountingHole"') == 2


# ------------------------------------------------------------------ stock case
CTX = {"board": {"width_mm": 50, "height_mm": 30, "thickness_mm": 1.6, "corner_radius_mm": 0},
       "mounting_holes": [],
       "parts": [{"ref": "J1", "kind": "port", "side": "top", "courtyard_mm": [20, 25, 30, 31], "height_mm": 3.2,
                  "nearest_wall": "bottom", "distance_to_edge_mm": 0, "_extra": {}, "value": "USB-C"},
                 {"ref": "D1", "kind": "led", "side": "top", "courtyard_mm": [5, 5, 7, 6], "height_mm": 0.8,
                  "nearest_wall": "top", "distance_to_edge_mm": 5, "_extra": {"hole": 3.0}},
                 {"ref": "U1", "kind": "part", "side": "top", "courtyard_mm": [30, 10, 40, 20], "height_mm": 12,
                  "nearest_wall": "right", "distance_to_edge_mm": 10, "_extra": {}}]}


def test_stock_case_fit_and_templates(tmp_path, monkeypatch):
    spec = {"name": "t", "board": {"case": {"name": "Box", "inner_mm": [60, 40, 15], "standoff_mm": 3}}}
    problems, warnings = case.check(spec, CTX)
    assert problems and "only 10.4 mm" in problems[0]
    o = case.openings(spec, CTX)
    cut = o["faces"]["front"][0]
    assert cut["ref"] == "J1" and cut["u"] == 24.5 and cut["v"] == 4.1 and cut["h"] == 4.2
    led = o["faces"]["lid"][0]
    assert (led["u"], led["v"]) == (11.0, 29.5)
    monkeypatch.setattr(case.enclosure, "context", lambda geo, spec, ph=None: CTX)
    spec["board"]["case"]["inner_mm"] = [60, 40, 25]
    r = case.build(spec, {}, tmp_path, "t")
    assert r["ok"] and r["headroom_mm"] == 8.4
    assert {f["face"] for f in r["files"]} == {"front", "lid"}
    svg = (tmp_path / "t-front-template.svg").read_text()
    assert 'width="88.0mm"' in svg and "50 mm" in svg
    spec["board"]["case"]["inner_mm"] = [40, 40, 25]
    assert "does not fit" in case.check(spec, CTX)[0][0]


# ------------------------------------------------------------------ other fabs
def test_fab_comparison():
    o = fabs.oshpark(50.8, 25.4, 2, 5)
    assert o["boards"] == 6 and o["total"] == 20.0 and o["shipping"] == "included"
    assert fabs.oshpark(50.8, 25.4, 4, 3)["total"] == 20.0
    assert fabs.oshpark(10, 10, 1, 3)["total"] is None
    assert fabs.pcbway(80, 80, 2, 5)["kind"] == "indicative"
    assert fabs.pcbway(150, 80, 2, 5)["total"] is None
    rows = fabs.compare({"board": {"width_mm": 50.8, "height_mm": 25.4, "layers": 2}},
                        {"quantity": 5, "pcb": {"subtotal": 2.0}, "shipping": {"subtotal": 3.0}})
    assert [r["fab"] for r in rows] == ["JLCPCB", "OSH Park", "PCBWay", "Aisler"] and rows[0]["total"] == 5.0


# ------------------------------------------------------------------ firmware build
def test_firmware_artifacts(tmp_path):
    b = tmp_path / ".pio" / "build" / "esp32c3"
    b.mkdir(parents=True)
    for n in ("bootloader.bin", "partitions.bin", "firmware.bin"):
        (b / n).write_bytes(b"x")
    a = fwbuild._artifacts(tmp_path, "esp32c3", "ESP32-C3")
    offs = {f["name"]: f["offset"] for f in a["files"]}
    assert a["kind"] == "esp" and a["chip"] == "esp32c3"
    assert offs["bootloader.bin"] == 0 and offs["partitions.bin"] == 0x8000 and offs["firmware.bin"] == 0x10000
    b2 = tmp_path / ".pio" / "build" / "esp32"
    b2.mkdir(parents=True)
    for n in ("bootloader.bin", "partitions.bin", "firmware.bin"):
        (b2 / n).write_bytes(b"x")
    assert {f["name"]: f["offset"] for f in fwbuild._artifacts(tmp_path, "esp32", "ESP32")["files"]}["bootloader.bin"] == 0x1000
    r = tmp_path / ".pio" / "build" / "pico"
    r.mkdir(parents=True)
    (r / "firmware.uf2").write_bytes(b"x")
    assert fwbuild._artifacts(tmp_path, "pico", "RP2040")["kind"] == "uf2"


def test_firmware_build_without_platformio(monkeypatch, tmp_path):
    monkeypatch.setattr(fwbuild, "find", lambda cfg=None: None)
    r = fwbuild.build(tmp_path, "esp32c3", "ESP32-C3")
    assert not r["ok"] and "not installed" in r["error"]


# ------------------------------------------------------------------ expert model
def test_expert_settings_and_toolless_session(monkeypatch):
    from wholeboard.ai import expert
    assert expert.settings({"expert": {"enabled": False, "model": "x"}}) is None
    cfg = expert.settings({"provider": "openai", "openai_model": "small", "expert": {"enabled": True, "provider": "anthropic",
                                                                                      "model": "claude-opus-5-5"}})
    assert cfg["provider"] == "anthropic" and cfg["anthropic_model"] == "claude-opus-5-5" and cfg["openai_model"] == "small"
    seen = {}

    class Sess:
        def __init__(self, c, system, tools):
            seen["tools"] = tools
            self.messages = []

        def add_user(self, t):
            self.messages.append({"role": "user", "content": t})

        def step(self):
            seen["msg"] = self.messages[-1]
            from wholeboard.ai.providers import Turn
            return Turn(text=" Use blocks. ", usage={"input": 10, "output": 5, "model": "claude-opus-5-5"})
    monkeypatch.setattr(expert, "make_session", Sess)
    text, usage = expert.ask(cfg, "plan", [{"media_type": "image/png", "data": "AAA"}])
    assert text == "Use blocks." and seen["tools"] == [] and seen["msg"]["content"][1]["type"] == "image"
    assert "usb_c_power" in expert.plan_prompt(["a board"], blocks.listing())


def test_agent_hides_expert_tool_without_expert(monkeypatch):
    from wholeboard.ai import agent
    names = [t["name"] for t in agent.TOOLS]
    assert {"list_blocks", "block_info", "consult_expert"} <= set(names)
    assert "Building blocks" in agent.SYSTEM and "board.case" in agent.SYSTEM


# ------------------------------------------------------------------ PDF report
def test_report_pdf(tmp_path):
    pytest.importorskip("reportlab")
    from wholeboard import report
    from wholeboard.project import Project
    state = {"id": "x", "title": "Test board", "revision": 1,
             "spec": {"name": "t", "summary": "A test.", "board": {"width_mm": 20, "height_mm": 10}, "components": [], "nets": []},
             "final_summary": "## Done\n- **bold** item\n`code`",
             "quote": {"total": 10.0, "per_board": 2.0, "quantity": 5, "pcb": {"subtotal": 2.0}, "shipping": {"subtotal": 8.0},
                       "fabs": [{"fab": "OSH Park", "boards": 6, "total": 5.0}]},
             "visuals": {"sim:sim": {"result": {"ran": True, "rails": [{"net": "+3V3", "volts": 3.3, "peak_load": 3.29, "ripple_v": 0.001}],
                                                "problems": [], "warnings": ["w"]}},
                         "sim:power": {"budget": {"supplies": [{"net": "VBUS", "current_ma": 10, "peak_ma": 20, "max_ma": 500,
                                                                "use_pct": 4}], "regulators": [], "battery": [], "notes": []}},
                         "outputs:bringup": {"steps": [{"title": "Look", "items": ["a"], "table": {"head": ["x"], "rows": [["1"]]}}]}},
             "artifacts": {"folder": str(tmp_path)}}
    p = Project(tmp_path, state)
    out = report.generate(p)
    assert out.read_bytes()[:4] == b"%PDF" and out.stat().st_size > 2000


# ------------------------------------------------------------------ placement fallback
def test_place_falls_back_to_full_mode(tmp_path, monkeypatch):
    from wholeboard import router
    calls = []

    def fake_stream(cmd, log, cancel, timeout):
        mode = cmd[cmd.index("--mode") + 1]
        calls.append((mode, timeout))
        if mode == "full":
            Path(cmd[cmd.index("-o") + 1]).write_text("pcb")
            return 0
        return -9
    monkeypatch.setattr(router, "find", lambda name, cfg=None: "/bin/tracemaker-place")
    monkeypatch.setattr(router, "_stream", fake_stream)
    monkeypatch.setattr(router, "_copy_project", lambda a, b: None)
    logs = []
    assert router.place(tmp_path / "in.kicad_pcb", tmp_path / "out.kicad_pcb", log=logs.append, seconds=10)["ok"]
    assert calls == [("routable", 150), ("full", 180)]
    assert "without the routing check" in logs[0]


@needs_libs
def test_test_points_fixed_along_an_edge_clear_of_holes():
    import math
    spec = design.normalise({"name": "t", "board": {"width_mm": 40, "height_mm": 30, "test_points": True,
                                                    "mounting_holes": {"diameter_mm": 3.2}},
                             "components": [{"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric",
                                             "lcsc": "C21190", "placement": {"x_mm": 12, "y_mm": 28}}],
                             "nets": [{"name": "VIN", "class": "power", "pins": ["R1.1"]},
                                      {"name": "GND", "class": "power", "pins": ["R1.2"]}]})
    pts = [(c["placement"]["x_mm"], c["placement"]["y_mm"]) for c in spec["components"] if c.get("test_point_for")]
    assert len(pts) == 2 and all(y == 28 for _, y in pts)
    assert all(math.hypot(x - 3.5, y - 26.5) >= 3.45 + 1.5 for x, y in pts)        # clear of hole H4
    assert all(not (12 - 1.48 - 1.5 < x < 12 + 1.48 + 1.5) for x, _ in pts)        # clear of R1
    assert math.dist(*pts) >= 3.0


@needs_libs
def test_fixed_part_edge_check_uses_the_real_pad_shapes():
    """A 1206's 1.15 x 1.8 mm pads near the edge: measured as rectangles, turned with the part, not as squares."""
    def errors(x, rot):
        spec = {"name": "t", "board": {"width_mm": 40, "height_mm": 30},
                "components": [{"ref": "C1", "value": "10uF", "footprint": "Capacitor_SMD:C_1206_3216Metric",
                                "lcsc": "C13585", "placement": {"x_mm": x, "y_mm": 15, "rotation": rot, "locked": True}}],
                "nets": [{"name": "A", "pins": ["C1.1"]}, {"name": "GND", "class": "power", "pins": ["C1.2"]}]}
        return [e for e in design.validate(spec).errors if "C1 at" in e]
    assert not errors(2.62, 180)          # pad edge 0.57 mm from the board edge (a square would say 0.24)
    assert errors(2.2, 180)               # 0.15 mm: too close
    assert not errors(1.5, 90)            # turned: the pads' 1.8 mm side runs along x, 0.6 mm from the edge
    assert errors(1.0, 90)


@needs_libs
def test_circle_courtyards_and_small_holes():
    from wholeboard import sexpr
    from wholeboard.kicad import board
    assert libs.load_footprint("MountingHole:MountingHole_3.2mm_M3").courtyard == (-3.45, -3.45, 3.45, 3.45)
    text = sexpr.dumps(board.footprint_instance(libs.load_footprint("RF_Module:ESP32-C3-WROOM-02"), "U1", "x", 0, 0))
    assert text.count("(drill 0.3)") == 12 and "(drill 0.2)" not in text


@needs_libs
def test_antenna_modules_keep_copper_away():
    """Every module with an antenna has a keep-out under it, so the router and the pour stay clear."""
    from wholeboard.kicad import libs
    mods = [c for b in blocks.BUILTIN for c in b["components"]
            if c.get("antenna") or str(c.get("footprint", "")).startswith("RF_Module:")]
    assert len(mods) >= 4
    for c in mods:
        fp = libs.load_footprint(c["footprint"])
        assert fp is not None and "keepout" in sexpr.dumps(fp.node), c["footprint"]


@needs_libs
@pytest.mark.parametrize("bid", ["ethernet_lan8720a", "usb_pd_ch224k_12v", "buck_tps563201_3v3", "stm32g030_min"])
def test_new_blocks_wire_up(bid, tmp_path):
    from wholeboard.kicad import cli, schematic
    b = blocks.catalogue()[bid]
    spec = design.normalise({"name": "t", "board": {"width_mm": 120, "height_mm": 120}, "blocks": [
        {"block": bid, "name": "B", "connect": {k: f"N_{k}" for k, v in b["ports"].items() if v.get("required")}}]})
    if bid == "ethernet_lan8720a":
        assert {tuple(i["nets"]) for i in spec["impedance"]} == {("B_TXP", "B_TXN"), ("B_RXP", "B_RXN"), (
            "N_TXD0", "N_TXD1", "N_TXEN", "N_RXD0", "N_RXD1", "N_CRS_DV", "N_REFCLK")}
    if bid == "usb_pd_ch224k_12v":
        vdd = next(n for n in spec["nets"] if n["name"] == "B_VDD")["pins"]
        assert any(p.endswith(".3") for p in vdd)          # CFG3 high asks for 12 V
    if cli.find_kicad_cli():
        sch = schematic.build(spec, tmp_path)
        erc = cli.erc(Path(sch["sch"]))
        real = [e for e in erc["errors"] if e["type"] != "pin_not_driven"]    # alone, nothing drives its inputs
        assert real == [], real


def test_stop_ends_a_quiet_router():
    import sys
    import threading
    import time
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.time()
    from wholeboard import router
    router._stream([sys.executable, "-c", "import time; time.sleep(30)"], lambda s: None, cancel, timeout=60)
    assert time.time() - start < 5                 # it printed nothing, but Stop still ended it


def test_ground_pads_left_to_the_pour_go_on_to_drc():
    from wholeboard.pipeline import _left_to_pour
    spec = {"board": {"ground_pour": "GND"}}
    rr = {"connections": 5, "routed": 3, "failures": ["GND: C1.2 -> zone  (search budget)",
                                                      "GND: U1.84 -> zone  (boxed in; fixed copper encloses the pin)"]}
    assert _left_to_pour(rr, spec)
    assert not _left_to_pour({**rr, "routed": 2}, spec)                         # an open one not listed
    assert not _left_to_pour({**rr, "failures": rr["failures"][:1] + ["GND: C1.2 -> C2.2  (boxed in)"]}, spec)
    assert not _left_to_pour({**rr, "failures": ["+3V3: C1.1 -> zone  (boxed in)"] * 2}, spec)   # not the pour's net
    assert not _left_to_pour(rr, {"board": {}})
    assert not _left_to_pour({**rr, "routed": 5, "failures": []}, spec)


def test_route_pours_only_when_tracemaker_can(tmp_path):
    from wholeboard import router
    new, old = tmp_path / "new-tm", tmp_path / "old-tm"
    new.write_text('#!/bin/sh\n[ "$1" = route ] && echo "  --pour TEXT ...  Add a copper pour before routing"\n')
    old.write_text('#!/bin/sh\nif [ "$1" = route ]; then echo "  --pours-off  not it"; else echo "  --pour"; fi\n')
    for f in (new, old):
        f.chmod(0o755)
    assert router.route_pours({"tools": {"tracemaker": str(new)}})
    assert not router.route_pours({"tools": {"tracemaker": str(old)}})     # only `route --help` counts
