"""The return-path check: a plane under each controlled net, with no voids or splits (issue #14)."""

import pytest

from wholeboard import design, impedance, returnpath


def _spec(layers=4, **group):
    nets = ["USB_DP", "USB_DN", "GND", "+3V3"]
    return design.normalise({"name": "t", "board": {"layers": layers}, "components": [],
                             "nets": [{"name": n, "pins": [], **({"class": "power"} if n == "+3V3" else {})} for n in nets],
                             "impedance": [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff", **group}]})


def _rect(net, layer, x0, y0, x1, y1):
    return {"net": net, "layer": layer, "pts": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def _geo(zones, layer="F.Cu", vias=()):
    return {"tracks": [{"a": [0, 5], "b": [20, 5], "w": 0.2, "net": "USB_DP", "layer": layer},
                       {"a": [0, 5.35], "b": [20, 5.35], "w": 0.2, "net": "USB_DN", "layer": layer}],
            "arcs": [], "vias": list(vias), "pads": [], "zones": zones}


def _rp(spec, geo):
    return impedance.check(spec, geo)["return_path"]


def test_a_solid_plane_under_the_pair_passes():
    r = _rp(_spec(), _geo([_rect("GND", "In1.Cu", -5, 0, 30, 10)]))
    assert [(n["net"], n["longest_void_mm"], n["splits"]) for n in r["nets"]] == [("USB_DP", 0, []), ("USB_DN", 0, [])]
    assert r["warnings"] == [] and r["nets"][0]["checked_mm"] == 20


def test_a_slot_in_the_plane_is_a_void():
    r = _rp(_spec(), _geo([_rect("GND", "In1.Cu", -5, 0, 8, 10), _rect("GND", "In1.Cu", 11, 0, 30, 10)]))
    dp = r["nets"][0]
    assert dp["longest_void_mm"] == pytest.approx(3, abs=0.25) and dp["splits"] == []   # GND on both sides: no split
    assert any(w.startswith("USB_DP runs 3.") and "no plane under it" in w for w in r["warnings"])
    assert _rp(_spec(max_void_mm=4), _geo([_rect("GND", "In1.Cu", -5, 0, 8, 10),
                                           _rect("GND", "In1.Cu", 11, 0, 30, 10)]))["warnings"] == []


def test_crossing_from_one_plane_to_another_is_a_split():
    r = _rp(_spec(), _geo([_rect("GND", "In1.Cu", -5, 0, 10, 10), _rect("+3V3", "In1.Cu", 10.3, 0, 30, 10)]))
    dp = r["nets"][0]
    assert dp["longest_void_mm"] < 1 and dp["splits"][0]["between"] == ["+3V3", "GND"]
    assert any("USB_DP crosses from the +3V3 plane to the GND plane" in w for w in r["warnings"])


def test_an_inner_trace_keeps_its_return_on_the_other_plane():
    """In1.Cu sits between F.Cu and In2.Cu: a split on one side is fine while the other plane is solid."""
    zones = [_rect("GND", "In2.Cu", -5, 0, 30, 10),
             _rect("GND", "F.Cu", -5, 0, 10, 10), _rect("+3V3", "F.Cu", 10.3, 0, 30, 10)]
    r = _rp(_spec(), _geo(zones, layer="In1.Cu"))
    assert r["warnings"] == [] and all(n["splits"] == [] for n in r["nets"])


def test_the_nets_own_via_clearance_is_not_a_void():
    zones = [_rect("GND", "In1.Cu", -5, 0, 9.4, 10), _rect("GND", "In1.Cu", 10.6, 0, 30, 10)]   # antipad round 10, 5
    via = {"p": [10, 5], "d": 0.6, "drill": 0.3, "net": "USB_DP"}
    r = _rp(_spec(), _geo(zones, vias=[via]))
    assert r["nets"][0]["longest_void_mm"] == 0 and r["nets"][1]["longest_void_mm"] > 1   # DN has no via there


def test_a_through_hole_pin_with_an_oval_drill_is_not_a_void():
    zones = [_rect("GND", "In1.Cu", -5, 0, 9.0, 10), _rect("GND", "In1.Cu", 11.0, 0, 30, 10)]   # cleared round 10, 5
    geo = _geo(zones)
    geo["pads"] = [{"ref": "J1", "name": "1", "x": 10, "y": 5, "w": 1.4, "h": 1.4, "rot": 0, "kind": "thru_hole",
                    "front": True, "back": True, "drill": 0, "net": "USB_DP"}]
    r = _rp(_spec(), geo)
    assert r["nets"][0]["longest_void_mm"] == 0 and r["nets"][1]["longest_void_mm"] > 1   # DN has no pin there


def test_signal_zones_are_not_planes_and_no_planes_is_a_note():
    r = _rp(_spec(), _geo([_rect("USB_DN", "In1.Cu", -5, 0, 30, 10)]))
    assert r["nets"] == [] and "not checked" in r["note"]
    r = _rp(_spec(), _geo([_rect("GND", "B.Cu", -5, 0, 30, 10)]))      # 4 layers: B.Cu is not next to F.Cu
    assert r["nets"] == [] and "F.Cu" in r["note"]
    r = _rp(_spec(layers=2), _geo([_rect("GND", "B.Cu", -5, 0, 30, 10)]))   # 2 layers: it is
    assert [n["longest_void_mm"] for n in r["nets"]] == [0, 0]


def test_a_fractured_fill_has_its_holes():
    # KiCad joins a hole to the outline with a zero-width cut: a 10 x 10 square with a 4 x 4 hole in the middle
    z = returnpath._Zone("GND", [(0, 0), (10, 0), (10, 10), (5, 10), (5, 7), (7, 7), (7, 3), (3, 3), (3, 7), (5, 7),
                                 (5, 10), (0, 10)])
    assert z.contains(1, 1) and z.contains(8, 8) and not z.contains(5, 5) and not z.contains(11, 5)


def test_a_large_fill_is_quick():
    import math
    import time
    ring = [(15 + 14 * math.cos(a / 2000 * 2 * math.pi), 5 + 14 * math.sin(a / 2000 * 2 * math.pi)) for a in range(2000)]
    geo = _geo([{"net": "GND", "layer": "In1.Cu", "pts": ring}])
    geo["tracks"] = geo["tracks"] * 50                                  # 2000 mm of controlled track
    start = time.time()
    _rp(_spec(), geo)
    assert time.time() - start < 3
