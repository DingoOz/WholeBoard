import pytest

from wholeboard import mains


def _spec(**over):
    spec = {"mains": {"nets": ["AC_L", "AC_N"], "volts": 230},
            "components": [{"ref": "F1", "value": "Fuse 1A"}, {"ref": "PS1", "value": "HLK-PM01"},
                           {"ref": "U1", "value": "LTV-814S"}, {"ref": "R1", "value": "100k"},
                           {"ref": "J2", "value": "Header"}],
            "nets": [{"name": "AC_IN", "pins": ["J1.1", "F1.1"]}, {"name": "AC_L", "pins": ["F1.2", "PS1.1", "R1.1"]},
                     {"name": "AC_N", "pins": ["PS1.2", "U1.2"]}, {"name": "LED", "pins": ["R1.2", "U1.1"]},
                     {"name": "GND", "pins": ["PS1.3", "U1.3", "J2.1"]}, {"name": "+5V", "pins": ["PS1.4", "J2.2"]},
                     {"name": "ZC", "pins": ["U1.4", "J2.3"]}]}
    spec.update(over)
    return spec


def test_requirements_230v():
    r = mains.requirements(mains.config(_spec()))
    assert (r["clearance_mm"], r["creepage_mm"], r["functional_outer_mm"]) == (3.0, 5.0, 2.5)
    r120 = mains.requirements(mains.config(_spec(mains={"nets": ["AC_L"], "volts": 120})))
    assert r120["creepage_mm"] < r["creepage_mm"] and r120["clearance_mm"] == 1.5


def test_sides_and_refusals():
    spec = _spec()
    hot, crossing = mains.sides(spec, mains.config(spec))
    assert hot == {"AC_L", "AC_N", "LED", "AC_IN"} or hot >= {"AC_L", "AC_N", "LED"}
    assert set(crossing) == {"PS1", "U1"}
    assert mains.validate(spec)[0] == []
    nofuse = _spec(components=[c for c in spec["components"] if c["ref"] != "F1"])
    assert "no fuse" in mains.validate(nofuse)[0][0]
    nobarrier = _spec(components=[{**c, "barrier": False} if c["ref"] in ("PS1", "U1") else c for c in spec["components"]])
    assert any("no isolation barrier" in e for e in mains.validate(nobarrier)[0])
    undeclared = {k: v for k, v in spec.items() if k != "mains"}
    assert "look like mains" in mains.validate(undeclared)[0][0]             # AC_L, AC_N and an HLK-PM01
    assert mains.validate({"nets": [{"name": "N_IN"}, {"name": "LINE_OUT"}], "components": []}) == ([], [])
    live = _spec(mains={"nets": ["AC_L", "AC_N"], "isolated": False})
    assert mains.validate(nobarrier | {"mains": live["mains"]})[0] == []


def _pad(ref, name, net, x, y, w=1.8, h=0.6):
    return {"ref": ref, "name": name, "net": net, "x": x, "y": y, "w": w, "h": h, "rot": 0, "shape": "rect",
            "kind": "smd", "front": True, "back": False}


def test_spacing_and_slot():
    spec = _spec()
    # the optocoupler: mains pins on the left, low-voltage pins 4.5 mm away on the right
    pads = [_pad("U1", "1", "LED", 0, 0), _pad("U1", "2", "AC_N", 0, 2.54),
            _pad("U1", "3", "GND", 6.3, 2.54), _pad("U1", "4", "ZC", 6.3, 0)]
    geo = {"layers": ["F.Cu", "B.Cu"], "pads": pads, "outline": []}
    rep = mains.check(spec, geo)
    assert rep["problems"] and "creepage" in rep["problems"][0]
    slots = mains.slots_for(spec, geo)
    assert len(slots) == 1 and slots[0]["fits"] and slots[0]["width"] == 1.0
    s = slots[0]
    assert s["centre"][0] == pytest.approx(3.15, abs=0.01) and s["length"] > 2.54 + 1.8
    # the slot as Edge.Cuts, read back the way geometry.read reports it
    outline = [{"kind": "rect", "start": [-20, -20], "end": [20, 20]}]
    for n in mains.slot_outline(s):
        pts = {str(k[0]): [float(k[1]), float(k[2])] for k in n[1:] if isinstance(k, list) and str(k[0]) in ("start", "mid", "end")}
        outline.append({"kind": str(n[0])[3:], **pts})
    geo["outline"] = outline
    assert len(mains.cutouts(geo)) == 1
    rep = mains.check(spec, geo)
    assert rep["problems"] == [], rep["problems"]
    row = next(r for r in rep["rows"] if r["a"].startswith("U1.2"))
    assert row["clearance_mm"] == pytest.approx(4.5, abs=0.01) and row["creepage_mm"] >= 5.0


def test_functional_spacing_between_mains_nets():
    spec = _spec()
    geo = {"layers": ["F.Cu", "B.Cu"], "outline": [],
           "pads": [_pad("PS1", "1", "AC_L", 0, 0), _pad("R9", "1", "AC_N", 2.8, 0)]}
    rep = mains.check(spec, geo)
    assert any("mains needs 2.5 mm" in p for p in rep["problems"])
    geo["pads"][1]["ref"] = "PS1"                # the same part's own pins are the maker's rating
    assert mains.check(spec, geo)["problems"] == []


def test_dru_and_netclasses():
    spec = _spec()
    text = "\n".join(mains.dru_rules(spec))
    assert "(constraint creepage (min 5mm))" in text and "Mains side" in text
    assert mains.netclasses(spec)["Mains"] == ["AC_L", "AC_N"]
    assert mains.dru_rules({"nets": [], "components": []}) == []
