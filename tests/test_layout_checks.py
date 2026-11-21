"""Checks on the routed board beyond DRC: converter input loops and crosstalk spacing (issue #14)."""

import pytest

from wholeboard import design, impedance

def _smd(ref, name, x, y, net):
    return {"ref": ref, "name": name, "x": x, "y": y, "w": 0.8, "h": 0.9, "rot": 0, "kind": "smd", "net": net}


def _buck(cap_x: float, boost: bool = False):
    """A buck converter U1 (VIN pad 1, GND pad 2, SW pad 3) with its input capacitor C1 cap_x mm to the right.
    boost: L1 runs from VIN to SW instead (a boost converter)."""
    pins_map = {"VIN": "1", "GND": "2", "SW": "3", "FB": "4"}
    spec = design.normalise({"name": "t", "board": {"width_mm": 40, "height_mm": 30},
                             "components": [{"ref": "U1", "value": "BUCK", "footprint": "Package_TO_SOT_SMD:SOT-23-6",
                                             "sim": {"model": "converter", "pins_map": pins_map, "vout": 3.3}},
                                            {"ref": "C1", "value": "10uF", "footprint": "Capacitor_SMD:C_0805_2012Metric"},
                                            {"ref": "L1", "value": "2.2uH", "footprint": "Inductor_SMD:L_1210_3225Metric"}],
                             "nets": [{"name": "VIN", "class": "power", "pins": ["U1.1", "C1.1"] + (["L1.1"] if boost else [])},
                                      {"name": "GND", "pins": ["U1.2", "C1.2"]},
                                      {"name": "SW", "pins": ["U1.3", "L1.2" if boost else "L1.1"]},
                                      {"name": "+3V3", "class": "power", "pins": ["U1.4"] + ([] if boost else ["L1.2"])}]})
    pads = [_smd("U1", "1", 10, 10, "VIN"), _smd("U1", "2", 10, 12, "GND"), _smd("U1", "3", 8, 11, "SW"),
            _smd("U1", "4", 8, 12, "+3V3"),
            _smd("C1", "1", 10 + cap_x, 10, "VIN"), _smd("C1", "2", 10 + cap_x, 12, "GND"),
            _smd("L1", "1", 4, 9, "VIN" if boost else "SW"), _smd("L1", "2", 4, 13, "SW" if boost else "+3V3")]
    return spec, {"pads": pads, "tracks": [], "arcs": [], "vias": []}


def test_a_converters_input_loop_is_measured():
    from wholeboard import checks
    spec, geo = _buck(2)                          # C1 2 mm from the pins: a 2 x 2 mm loop
    r = checks.run(spec, geo)
    loop = r["hot_loops"][0]
    assert (loop["ref"], loop["cap"], loop["area_mm2"]) == ("U1", "C1", 4.0) and 0 < loop["loop_nh"] < 10
    assert not any("input loop" in w for w in r["warnings"])
    spec, geo = _buck(15)                         # 15 mm away: 30 mm²
    r = checks.run(spec, geo)
    assert r["hot_loops"][0]["area_mm2"] == 30.0
    assert any("U1's input loop (VIN through C1 to GND) encloses about 30 mm²" in w for w in r["warnings"])
    spec["rules"]["hot_loop_mm2"] = 40           # a looser limit for this board
    assert not any("input loop" in w for w in checks.run(spec, geo)["warnings"])
    geo["pads"] += [_smd("CN1", "1", 11, 10, "VIN"), _smd("CN1", "2", 11, 12, "GND")]   # a connector, not a capacitor
    assert checks.run(spec, geo)["hot_loops"][0]["cap"] == "C1"


def test_a_boosts_input_side_is_not_its_hot_loop():
    from wholeboard import checks
    spec, geo = _buck(15, boost=True)
    assert checks.run(spec, geo)["hot_loops"] == []


def test_loop_inductance_grows_with_the_area():
    from wholeboard import checks
    assert checks.loop_inductance_nh(0) == 0
    assert 0 < checks.loop_inductance_nh(4) < checks.loop_inductance_nh(30) < checks.loop_inductance_nh(100)
    assert checks._loop_area([(0, 0), (2, 0), (2, 3), (0, 3)]) == 6


def _bus_spec(**group):
    nets = ["RGMII_TXD0", "RGMII_TXD1", "LED_STAT", "GND", "+3V3"]
    return design.normalise({"name": "t", "board": {"layers": 4}, "components": [],
                             "nets": [{"name": n, "pins": [], **({"class": "power"} if n == "+3V3" else {})} for n in nets],
                             "impedance": [{"nets": ["RGMII_TXD0", "RGMII_TXD1"], "kind": "bus", **group}]})


def _track(net, y, x0=0, x1=20, w=0.2, layer="F.Cu"):
    return {"a": [x0, y], "b": [x1, y], "w": w, "net": net, "layer": layer}


def test_fast_nets_close_to_other_signals_break_the_3w_rule():
    spec = _bus_spec()
    geo = {"tracks": [_track("RGMII_TXD0", 0), _track("RGMII_TXD1", 0.4),       # 0.4 mm apart, 3W wants 0.6 mm
                      _track("LED_STAT", -0.5, 5, 9),                             # beside TXD0 for only 4 mm
                      _track("GND", -0.3), _track("+3V3", 0.8, 0, 20),            # ground and power don't count
                      _track("LED_STAT", 1.0, 0, 20, layer="B.Cu")],              # another layer
           "arcs": [], "vias": []}
    r = impedance.check(spec, geo)
    assert [(s["net"], s["other"], s["parallel_mm"], s["closest_mm"], s["needs_mm"]) for s in r["spacing"]] == \
        [("RGMII_TXD0", "RGMII_TXD1", 20.0, 0.4, 0.6)]                           # the bus pair reported once
    assert any("RGMII_TXD0 runs 20 mm beside RGMII_TXD1" in w and "3 track widths" in w for w in r["warnings"])
    geo["tracks"][1] = _track("RGMII_TXD1", 0.7)                                  # 0.7 mm: clear
    assert impedance.check(spec, geo)["spacing"] == []
    geo["tracks"][1] = _track("RGMII_TXD1", 0.4)
    assert impedance.check(_bus_spec(spacing_w=0), geo)["spacing"] == []          # turned off
    assert impedance.check(_bus_spec(max_parallel_mm=25), geo)["spacing"] == []   # a longer run allowed


def test_a_diff_pairs_own_halves_are_not_crosstalk():
    spec = design.normalise({"name": "t", "board": {"layers": 4}, "components": [],
                             "nets": [{"name": n, "pins": []} for n in ("USB_DP", "USB_DN", "SPI_CLK")],
                             "impedance": [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff"}]})
    w = impedance.rules(spec)[0]["width_mm"]
    geo = {"tracks": [_track("USB_DP", 0, w=w), _track("USB_DN", w + 0.15, w=w),
                      _track("SPI_CLK", -(2 * w), 2, 12, w=0.2)],                  # 2W from DP for 10 mm
           "arcs": [], "vias": []}
    close = impedance.check(spec, geo)["spacing"]
    assert [(s["net"], s["other"], s["parallel_mm"]) for s in close] == [("USB_DP", "SPI_CLK", 10.0)]


def test_alongside_counts_only_the_near_part_of_a_slanted_run():
    t = _track("A", 0, 0, 10)
    s = {"a": [0, 0.2], "b": [10, 1.0], "w": 0.2, "net": "B", "layer": "F.Cu"}   # 4.6° off, drifting away
    run, closest = impedance._alongside(t, s, 0.6)
    assert run == pytest.approx(5.0) and closest == pytest.approx(0.2)
    crossing = {"a": [5, -5], "b": [5, 5], "w": 0.2, "net": "B", "layer": "F.Cu"}
    assert impedance._alongside(t, crossing, 0.6) == (0.0, 0.0)
