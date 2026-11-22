"""Rail voltages at the load pins, through the routed copper (issue #14)."""

import random

import pytest

from wholeboard import checks, design, railvolts


# ------------------------------------------------------------------ the network solver
def test_series_and_parallel_drops():
    n = railvolts._Network()
    n.link("s", "a", 0.1)
    n.link("a", "b", 0.2)
    n.link("a", "b", 0.2)                  # two 0.2 Ω paths in parallel: 0.1 Ω
    d = n.drops("s", {"b": 2.0, "a": 1.0})
    assert d["a"] == pytest.approx(3.0 * 0.1) and d["b"] == pytest.approx(0.3 + 2.0 * 0.1)


def test_the_solver_matches_a_dense_solve():
    """A random meshed network against plain Gaussian elimination of the full matrix."""
    rng = random.Random(4)
    n, net = 40, railvolts._Network()
    for i in range(1, n):                                    # a tree joined to the source, plus extra loops
        net.link(i, rng.randrange(0, i), rng.uniform(0.001, 0.1))
    for _ in range(30):
        a, b = rng.sample(range(n), 2)
        net.link(a, b, rng.uniform(0.001, 0.1))
    sinks = {i: rng.uniform(0, 0.5) for i in rng.sample(range(1, n), 12)}
    got = net.drops(0, sinks)
    free = list(range(1, n))
    A = [[0.0] * len(free) for _ in free]
    rhs = [sinks.get(i, 0.0) for i in free]
    for r, i in enumerate(free):
        for j, g in net.adj[i].items():
            A[r][r] += g
            if j != 0:
                A[r][free.index(j)] -= g
    for c in range(len(free)):                               # Gauss-Jordan
        p = max(range(c, len(free)), key=lambda r: abs(A[r][c]))
        A[c], A[p], rhs[c], rhs[p] = A[p], A[c], rhs[p], rhs[c]
        for r in range(len(free)):
            if r != c and A[r][c]:
                f = A[r][c] / A[c][c]
                A[r] = [x - f * y for x, y in zip(A[r], A[c])]
                rhs[r] -= f * rhs[c]
    for r, i in enumerate(free):
        assert got[i] == pytest.approx(rhs[r] / A[r][r], rel=1e-9, abs=1e-12)


# ------------------------------------------------------------------ on a board
def _pad(ref, name, x, y, net, layer="F.Cu", drill=0):
    return {"ref": ref, "name": name, "x": x, "y": y, "w": 1.0, "h": 1.0, "rot": 0, "kind": "smd", "net": net,
            "front": layer == "F.Cu", "back": layer == "B.Cu", "drill": drill}


def _t(a, b, net="+3V3", w=0.2, layer="F.Cu"):
    return {"a": list(a), "b": list(b), "w": w, "net": net, "layer": layer}


def _ldo_board(extra_comps=(), extra_nets=()):
    """U1 an LDO (VI pad 1, VO pad 2, GND pad 3) feeding U2's two supply pins on +3V3."""
    return design.normalise({
        "name": "t", "board": {"width_mm": 80, "height_mm": 40},
        "components": [{"ref": "U1", "value": "LDO", "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                        "sim": {"model": "ldo", "pins_map": {"VI": "1", "VO": "2", "GND": "3"}, "vout": 3.3}},
                       {"ref": "U2", "value": "MCU", "footprint": "Package_QFP:LQFP-48_7x7mm_P0.5mm",
                        "sim": {"model": "ic"}}, *extra_comps],
        "nets": [{"name": "+5V", "class": "power", "pins": ["U1.1"]},
                 {"name": "+3V3", "class": "power", "pins": ["U1.2", "U2.1", "U2.2"]},
                 {"name": "GND", "pins": ["U1.3", "U2.3"]}, *extra_nets]})


def _sim(peak_ma=200):
    return {"rails": [{"net": "+3V3", "volts": 3.3}],
            "loads": [{"ref": "U2", "nets": ["+3V3"], "typ_ma": peak_ma / 2, "peak_ma": peak_ma}], "parts": []}


R_50MM = 1.72e-8 * 0.050 / (0.2e-3 * 0.035e-3)               # 50 mm of 0.2 mm, 1 oz copper: 0.123 Ω


def _geo(tracks, vias=()):
    pads = [_pad("U1", "1", -5, 0, "+5V"), _pad("U1", "2", 0, 0, "+3V3"), _pad("U1", "3", 0, 5, "GND"),
            _pad("U2", "1", 50, 0, "+3V3"), _pad("U2", "2", 50, 10, "+3V3"), _pad("U2", "3", 55, 5, "GND")]
    return {"pads": pads, "tracks": list(tracks), "arcs": [], "vias": list(vias), "zones": []}


def test_the_far_pin_sees_the_copper_drop():
    spec = _ldo_board()
    # U1.2 -> U2.1 50 mm, and a tee 30 mm along it down to U2.2 (a 20 mm stub, then 10 mm)
    geo = _geo([_t((0, 0), (50, 0)), _t((30, 0), (30, 10)), _t((30, 10), (50, 10))])
    r = checks.run(spec, geo, sim_res=_sim(200))
    rail = r["rails"][0]
    assert rail["net"] == "+3V3" and rail["sources"] == ["U1.2"] and rail["current_ma"] == 200
    pins = {p["pad"]: p for p in rail["pins"]}
    # 100 mA to each pin: the shared 30 mm carries 200 mA, each branch 100 mA
    shared = 0.2 * R_50MM * 30 / 50
    assert pins["1"]["drop_mv"] == pytest.approx((shared + 0.1 * R_50MM * 20 / 50) * 1000, rel=1e-3)
    assert pins["2"]["drop_mv"] == pytest.approx((shared + 0.1 * R_50MM * 30 / 50) * 1000, rel=1e-3)
    assert rail["worst"]["pad"] == "2" and not rail["unreached"]
    assert not any("below the 3.3 V" in w for w in r["warnings"])       # 0.6 % of 3.3 V


def test_a_drop_over_the_limit_is_warned_once():
    spec = _ldo_board()
    geo = _geo([_t((0, 0), (50, 0)), _t((50, 0), (50, 10))])
    r = checks.run(spec, geo, net_current_ma={"+3V3": 1000}, sim_res=_sim(1000))
    warns = [w for w in r["warnings"] if "drop" in w or "below the" in w]
    assert len(warns) == 1 and "U2 pin 2 gets 3.1" in warns[0] and "rules.rail_drop_pct" in warns[0]
    spec["rules"]["rail_drop_pct"] = 10
    assert not any("below the" in w for w in checks.run(spec, geo, net_current_ma={"+3V3": 1000},
                                                             sim_res=_sim(1000))["warnings"])


def test_a_via_joins_the_layers():
    spec = _ldo_board()
    via = {"p": [25, 0], "d": 0.6, "drill": 0.3, "net": "+3V3"}
    geo = _geo([_t((0, 0), (25, 0)), _t((25, 0), (50, 0), layer="B.Cu"), _t((50, 0), (50, 10), layer="B.Cu")], [via])
    geo["pads"][3:5] = [_pad("U2", "1", 50, 0, "+3V3", layer="B.Cu"), _pad("U2", "2", 50, 10, "+3V3", layer="B.Cu")]
    rail = checks.run(spec, geo, sim_res=_sim(200))["rails"][0]
    assert not rail["unreached"]
    via_r = railvolts._via_r(0.3, 1.6)
    pin1 = next(p for p in rail["pins"] if p["pad"] == "1")
    assert pin1["drop_mv"] == pytest.approx(0.2 * (R_50MM + via_r) * 1000, rel=1e-3)


def test_a_pin_the_tracks_do_not_reach_is_named():
    spec = _ldo_board()
    rail = checks.run(spec, _geo([_t((0, 0), (50, 0))]), sim_res=_sim(200))["rails"][0]
    assert rail["unreached"] == ["U2.2"]


def test_a_buck_feeds_its_rail_from_the_inductor_and_loads_its_input():
    spec = _ldo_board(extra_comps=[
        {"ref": "U3", "value": "BUCK", "footprint": "Package_TO_SOT_SMD:SOT-23-6",
         "sim": {"model": "converter", "pins_map": {"VIN": "1", "SW": "2", "GND": "3", "FB": "4"}, "vout": 1.2}},
        {"ref": "L1", "value": "2.2uH", "footprint": "Inductor_SMD:L_1210_3225Metric"}],
        extra_nets=[{"name": "SW", "pins": ["U3.2", "L1.1"]}, {"name": "+1V2", "class": "power", "pins": ["L1.2", "U3.4"]}])
    spec["nets"] = [{**n, "pins": n["pins"] + ["U3.1"]} if n["name"] == "+3V3" else n for n in spec["nets"]]
    geo = _geo([_t((0, 0), (50, 0)), _t((50, 0), (50, 10)), _t((50, 10), (40, 20)),
                _t((70, 20), (80, 20), net="+1V2")])
    geo["pads"] += [_pad("U3", "1", 40, 20, "+3V3"), _pad("U3", "2", 60, 20, "SW"), _pad("L1", "1", 65, 20, "SW"),
                    _pad("L1", "2", 70, 20, "+1V2"), _pad("U3", "4", 80, 20, "+1V2")]
    sim_res = {**_sim(200), "parts": [{"ref": "U3", "kind": "converter", "vin": 3.3, "vout": 1.2,
                                       "current_ma": 850, "peak_ma": 850}]}
    rails = {r["net"]: r for r in checks.run(spec, geo, sim_res=sim_res)["rails"]}
    u3 = next(p for p in rails["+3V3"]["pins"] if p["ref"] == "U3")
    assert u3["ma"] == pytest.approx(850 * 1.2 / (0.85 * 3.3), rel=1e-3)   # 364 mA in for 850 mA out
    assert rails["+3V3"]["current_ma"] == pytest.approx(200 + 363.6, abs=0.2)


def test_poured_rails_and_no_simulation_are_left_to_the_estimate():
    spec = _ldo_board()
    geo = _geo([_t((0, 0), (50, 0)), _t((50, 0), (50, 10))])
    r = checks.run(spec, geo, net_current_ma={"+3V3": 1000})
    assert r["rails"] == [] and "No simulation results" in r["rails_note"]
    assert any("mV drop along" in w for w in r["warnings"])            # the older estimate still warns
    geo["zones"] = [{"net": "+3V3", "layer": "B.Cu", "pts": [[0, 0], [1, 0], [1, 1]]}]
    assert checks.run(spec, geo, sim_res=_sim(1000))["rails"] == []


def test_a_through_hole_pad_with_an_oval_drill_joins_both_sides():
    """A connector's oval drill reads as 0; the pad still joins the bottom track to the top one."""
    spec = _ldo_board()
    geo = _geo([_t((0, 0), (25, 0)), _t((25, 0), (50, 0), layer="B.Cu"), _t((50, 0), (50, 10), layer="B.Cu")])
    geo["pads"].append({**_pad("U1", "2", 25, 0, "+3V3"), "ref": "J1", "name": "1", "kind": "thru_hole",
                        "front": True, "back": True, "drill": 0})
    spec["components"].append({"ref": "J1", "value": "CONN", "footprint": "Connector:Banana_Jack_1Pin"})
    spec["nets"] = [{**n, "pins": n["pins"] + ["J1.1"]} if n["name"] == "+3V3" else n for n in spec["nets"]]
    geo["pads"][3:5] = [_pad("U2", "1", 50, 0, "+3V3", layer="B.Cu"), _pad("U2", "2", 50, 10, "+3V3", layer="B.Cu")]
    rail = checks.run(spec, geo, sim_res=_sim(200))["rails"][0]
    assert not rail["unreached"] and {p["pad"] for p in rail["pins"]} == {"1", "2"}
