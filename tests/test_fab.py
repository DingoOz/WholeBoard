"""Manufacturing: JLCPCB's design rules, the features it charges extra for."""

import json
from pathlib import Path

import pytest

from wholeboard import design
from wholeboard.jlc import quote
from wholeboard.kicad import dru, libs

SPEC = json.loads((Path(__file__).parent / "sample_spec.json").read_text())
HAVE_LIBS = libs.locate("footprints") is not None


def test_rules_file_follows_the_layer_count():
    two, four = dru.text(2), dru.text(4, 1.2)
    assert "(version 1)" in two and 'rule "JLCPCB: Via drill"' in two
    assert "(constraint hole_size (min 0.3mm))" in two and "(constraint hole_size (min 0.15mm))" in four
    assert "annular_width" in two and "hole_to_hole" in two and "silk_clearance" in two and "text_height" in two
    assert "(constraint edge_clearance (min 0.5mm))" in dru.text(2, 1.6, 0.5)     # the spec's larger edge clearance
    assert dru.limits(4, 2.0)["min_via_drill"] == 0.2          # 10:1 aspect ratio on a 2 mm board
    assert two.count("(") == two.count(")")
    assert "(constraint clearance" not in four                 # would replace the net classes' clearance


def test_small_vias_and_vias_in_pads_are_priced():
    pad = {"ref": "U1", "name": "5", "x": 10, "y": 10, "w": 1.0, "h": 0.4, "rot": 0, "kind": "smd"}
    geo = {"pads": [pad, {**pad, "name": "1", "kind": "thru_hole", "x": 20}],
           "vias": [{"p": [10.3, 10.0], "d": 0.45, "drill": 0.2}, {"p": [20, 10], "d": 0.6, "drill": 0.3},
                    {"p": [15, 15], "d": 0.6, "drill": 0.3}]}
    four = {f["feature"]: f for f in dru.cost_features(geo, 4)}
    assert four["small_via"]["count"] == 1 and four["small_via"]["amount"] > 0
    assert four["via_in_pad"]["pads"] == ["U1.5"] and four["via_in_pad"]["amount"] > 0
    two = {f["feature"]: f for f in dru.cost_features(geo, 2)}
    assert two["small_via"]["problem"] and two["via_in_pad"]["problem"]          # not made on 2 layers
    assert dru.cost_features({"pads": [pad], "vias": [{"p": [15, 15], "drill": 0.3}]}, 2) == []


def test_ipc2152_is_gentler_on_inner_layers_than_ipc2221():
    from wholeboard import checks
    outer = checks.trace_rise_c(1.0, 0.3, 0.035, False)
    assert 7 < outer < 12                                           # about the same as IPC-2221 on an outer layer
    assert checks.trace_rise_c(1.0, 0.3, 0.035, True) < checks.trace_rise_c(1.0, 0.3, 0.035, True, "ipc2221") / 3
    w = checks.width_for(1.0, 10, 0.035, False)
    assert checks.trace_rise_c(1.0, w, 0.035, False) == __import__("pytest").approx(10, rel=0.01)


def _fp(ref, x, y, rot=0, side="F.Cu", lib="Resistor_SMD:R_0603_1608Metric"):
    return {"ref": ref, "lib": lib, "x": x, "y": y, "rot": rot, "side": side}


def _pad(ref, name, x, y, net):
    return {"ref": ref, "name": name, "x": x, "y": y, "w": 0.8, "h": 0.9, "rot": 0, "kind": "smd", "net": net}


def test_hand_edits_are_read_back_and_kept():
    from wholeboard.kicad import handedit
    before = {"footprints": [_fp("R1", 110, 110), _fp("R2", 120, 110), _fp("C1", 130, 110)],
              "pads": [_pad("R1", "1", 109.2, 110, "A"), _pad("R2", "1", 119.2, 110, "A"),
                       _pad("C1", "1", 129.2, 110, "B")],
              "tracks": [{"a": [109.2, 110], "b": [119.2, 110], "w": 0.25, "layer": "F.Cu", "net": "A"},
                         {"a": [130.8, 110], "b": [135, 110], "w": 0.25, "layer": "F.Cu", "net": "B"}],
              "arcs": [], "vias": []}
    after = {**before, "footprints": [_fp("R1", 110, 110), _fp("R2", 120, 110), _fp("C1", 131, 112, 90)],
             "tracks": [{"a": [119.2, 110], "b": [109.2, 110], "w": 0.25, "layer": "F.Cu", "net": "A"},   # same, reversed
                        {"a": [109.2, 110], "b": [119.2, 115], "w": 0.4, "layer": "B.Cu", "net": "B"}]}
    d = handedit.diff(before, after)
    assert set(d["moved"]) == {"C1"} and d["moved"]["C1"] == {"x_mm": 31, "y_mm": 12, "rotation": 90, "side": "top",
                                                             "locked": True, "from": "kicad",
                                                             "footprint": "Resistor_SMD:R_0603_1608Metric"}
    assert set(d["nets"]) == {"B"} and d["nets"]["B"]["tracks"][0]["a"] == [9.2, 10]
    assert not d["held"]          # B's new track ends on R1's pad, but R1's pad is on net A
    kept = handedit.merge(None, d)
    spec = {"components": [{"ref": "C1", "footprint": "Resistor_SMD:R_0603_1608Metric"}, {"ref": "R1"}],
            "nets": [{"name": "A"}, {"name": "B"}]}
    out, notes = handedit.apply(spec, kept)
    assert out["components"][0]["placement"]["x_mm"] == 31 and out["fixed_copper"][0]["net"] == "B" and not notes
    assert "placement" not in spec["components"][0]                     # the AI's spec is untouched
    assert handedit.describe(kept) == "Kept 1 hand-placed part and 1 routed net (B) from KiCad"
    swapped, notes = handedit.apply({**spec, "components": [{"ref": "C1", "footprint": "Capacitor_SMD:C_0805"}]}, kept)
    assert "placement" not in swapped["components"][0] and "new footprint" in notes[0]
    # a later edit that removes B's copper lets the net go
    later = handedit.merge(kept, {"moved": {}, "held": {}, "nets": {}, "cleared": ["B"]})
    assert later["nets"] == {} and "C1" in later["parts"]


def test_kept_copper_is_written_locked(tmp_path):
    from wholeboard import design
    from wholeboard.kicad import board
    spec = design.normalise({"name": "k", "board": {"width_mm": 20, "height_mm": 20},
                             "components": [], "nets": [{"name": "B", "pins": []}],
                             "fixed_copper": [{"net": "B", "tracks": [{"a": [1, 1], "b": [5, 1], "w": 0.3, "layer": "F.Cu"}],
                                               "vias": [{"p": [5, 1], "d": 0.6, "drill": 0.3}]}]})
    text = (tmp_path / "k.kicad_pcb").read_text() if board.build(spec, tmp_path) else ""
    assert "(segment" in text and "(locked yes)" in text and "(via" in text and "(start 101 101)" in text
    assert (tmp_path / "k.kicad_dru").exists()


def test_bottom_parts_are_flipped_and_priced(tmp_path):
    from wholeboard import design
    from wholeboard.jlc import quote
    from wholeboard.kicad import board, geometry
    spec = design.normalise({"name": "b", "board": {"width_mm": 30, "height_mm": 20},
                             "components": [{"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric",
                                             "lcsc": "C21190",
                                             "placement": {"x_mm": 10, "y_mm": 10, "rotation": 90, "side": "bottom"}},
                                            {"ref": "R2", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric",
                                             "placement": {"x_mm": 10, "y_mm": 10.2, "rotation": 90}}],
                             "nets": [{"name": "A", "pins": ["R1.1", "R2.1"]}, {"name": "B", "pins": ["R1.2", "R2.2"]}]})
    assert design.validate(spec).ok                    # one on each side: they may overlap
    geo = geometry.read((tmp_path / "b.kicad_pcb") if board.build(spec, tmp_path) else None)
    f = {x["ref"]: x for x in geo["footprints"]}
    assert f["R1"]["side"] == "B.Cu" and f["R2"]["side"] == "F.Cu"
    p1 = [p for p in geo["pads"] if p["ref"] == "R1"]
    assert all(p["back"] and not p["front"] for p in p1)
    text = (tmp_path / "b.kicad_pcb").read_text()
    assert "B.SilkS" in text and "(justify mirror)" in text
    q = quote.full_quote(spec, {"quantity": 5, "assembly": True, "pcba_type": "economic"}, geo=geo)
    assert q["assembly"]["type"] == "standard" and q["assembly"]["bottom_parts"] == ["R1"]
    assert any(l["item"].startswith("Bottom-side assembly") for l in q["assembly"]["lines"])


def test_interface_profiles():
    from wholeboard import impedance
    g = impedance.design_rule({"nets": ["USB_DP", "USB_DN"], "kind": "diff", "profile": "usb2_hs"}, 4, 1.6, 0.15)
    assert g["ohms"] == 90 and g["max_vias"] == 2 and g["max_stub_mm"] == 2.5 and g["profile_title"].startswith("USB")
    own = impedance.design_rule({"nets": ["A", "B"], "kind": "diff", "profile": "usb2_hs", "max_vias": 4}, 4, 1.6, 0.15)
    assert own["max_vias"] == 4
    spec = {"board": {"layers": 4, "thickness_mm": 1.6}, "rules": {"clearance_mm": 0.15},
            "nets": [{"name": n} for n in ("USB_DP", "USB_DN", "D0", "D1")],
            "impedance": [{"nets": ["USB_DP", "USB_DN"], "kind": "diff", "profile": "usb2_hs"},
                          {"nets": ["D0", "D1"], "kind": "bus", "profile": "qspi_flash"}]}
    assert impedance.validate(spec)[0] == [] and len(impedance.netclasses(spec)) == 1
    two = {**spec, "nets": spec["nets"] + [{"name": "B_DP"}, {"name": "B_DN"}],
           "impedance": spec["impedance"] + [{"nets": ["B_DP", "B_DN"], "kind": "diff", "profile": "usb2_hs"}]}
    assert [k["nets"] for k in impedance.netclasses(two)] == [["USB_DP", "USB_DN", "B_DP", "B_DN"]]  # one class per kind
    bad = {**spec, "impedance": [{"nets": ["D0"], "profile": "nope"}]}
    assert "unknown profile" in impedance.validate(bad)[0][0]
    t = lambda net, a, b: {"net": net, "a": a, "b": b, "w": 0.2}  # noqa: E731
    geo = {"tracks": [t("USB_DP", (0, 0), (50, 0)), t("USB_DP", (25, 0), (25, 6)),      # 6 mm tee to an ESD pad
                      t("USB_DN", (0, 1), (50, 1)), t("D0", (0, 5), (40, 5)), t("D1", (0, 6), (20, 6))],
           "vias": [{"net": "USB_DN", "p": (1, 1)}] * 3}
    rep = impedance.check(spec, geo)
    dp = rep["groups"][0]["members"][0]
    assert dp["stub_mm"] == 6 and dp["length_mm"] == 56
    w = " ".join(rep["warnings"])
    assert "6 mm branch" in w and "3 vias" in w and "D0, D1: lengths differ by 20" in w and "40 mm long" in w


def test_blocks_carry_profiles():
    from wholeboard import blocks
    by = blocks.catalogue()
    assert by["usb_c_data"]["impedance"][0]["profile"] == "usb2_hs"
    assert {g["profile"] for g in by["ethernet_lan8720a"]["impedance"]} == {"ethernet_100", "rmii"}
    assert by["rp2040_min"]["impedance"][0]["kind"] == "bus"


def test_panel_arguments():
    from wholeboard.kicad import panelize
    tabs = " ".join(panelize.args(2, 3, vcut=False))
    assert "rows: 3; cols: 2; space: 2.0mm" in tabs and "mousebites" in tabs and "railstb; width: 5.0mm" in tabs
    assert "3hole" in tabs and "size: 1.152mm" in tabs and "3fid" in tabs and "millradius: 0.5mm" in tabs
    vcut = " ".join(panelize.args(2, 2, vcut=True, fiducials=False))
    assert "vcuts" in vcut and "layer: Cmts.User" in vcut and "space: 0mm" in vcut and "3fid" not in vcut
    assert panelize.copies("R1", 3) == ["R1_0", "R1_1", "R1_2"]


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_fiducials_and_fine_pitch_warning():
    spec = json.loads(json.dumps(SPEC))
    spec["components"].append({"ref": "U9", "value": "X", "footprint": "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm"})
    rep = design.validate(spec)
    assert any("fiducials" in w for w in rep.warnings)
    spec["board"]["fiducials"] = True
    n = design.normalise(spec)
    fids = [c for c in n["components"] if c.get("fiducial")]
    assert len(fids) == 3 and all(c["placement"]["locked"] for c in fids)
    corners = {(c["placement"]["x_mm"] < 15, c["placement"]["y_mm"] < 11) for c in fids}
    assert len(corners) == 3                                 # three different corners
    assert not any("fiducials" in w for w in design.validate(spec).warnings)
    assert not any(ref.startswith("FID") for r in quote.bom_rows(n) for ref in r["refs"])
