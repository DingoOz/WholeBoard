"""Heat in an enclosure: the box model, finding the box round the board, and the Sim Lab's box controls."""

import pytest

from wholeboard import boxheat, design, simlab
from test_simlab import _res, _spec


def _geo(spec):
    fp = lambda ref, lib, x, y: {"ref": ref, "lib": lib, "x": x, "y": y, "rot": 0, "side": "F.Cu",
                                  "court": [x - 1, y - 1, x + 1, y + 1]}
    c = spec["components"]
    return {"bbox": [100, 50, 130, 70],
            "outline": [{"kind": "rect", "start": [100, 50], "end": [130, 70]}],
            "footprints": [fp("J1", c[0]["footprint"], 101.5, 60), fp("R1", c[1]["footprint"], 110, 55),
                           fp("R2", c[2]["footprint"], 110, 65), fp("D1", c[3]["footprint"], 118, 60),
                           fp("U1", c[4]["footprint"], 124, 60)],
            "pads": [{"ref": "J1", "name": "1", "x": 101.5, "y": 59, "w": 1.7, "h": 1.7, "rot": 0, "shape": "rect",
                      "kind": "thru_hole", "drill": 1.0, "front": True, "back": True, "net": "+5V"}],
            "tracks": [{"a": [101.5, 59], "b": [118, 59], "w": 0.5, "layer": "F.Cu", "net": "+5V"}],
            "vias": [], "zones": []}


def _box(**kw):
    box = {"board_mm": [30, 20], "kind": "printed", "name": "the printed enclosure", "outer": [-3, -3, 53, 43], "z0": -6, "z1": 14,
           "wall": 2.0, "floor": 1.6, "lid": 1.6, "material": "PLA", "feet": False, "lid_slots": [], "lid_holes": [],
           "side": [], "designed_vents": {"lid": False, "sides": False}}
    return {**box, **kw}


def test_more_heat_and_fewer_openings_run_warmer():
    box = _box()
    a, b = boxheat.solve(box, 0.5, 25), boxheat.solve(box, 1.0, 25)
    assert 25 < a["air_c"] < b["air_c"]
    assert a["through_walls_w"] + a["through_openings_w"] == pytest.approx(0.5, rel=0.02)    # heat balance
    vented = boxheat.solve(box, 1.0, 25, "both")
    assert vented["air_c"] < b["air_c"] - 2 and vented["flow_l_min"] > 0 and vented["lid_slots"] and vented["side_slots"]
    assert boxheat.solve(box, 1.0, 25, placement="raised")["air_c"] < b["air_c"]
    # a sealed plastic box sits near the usual rule of thumb: rise = P / (5.5 W/m²K × the area that is not on the desk)
    area = (0.056 * 0.046) + 2 * (0.056 + 0.046) * 0.020
    assert 0.6 < b["rise_k"] / (1.0 / (5.5 * area)) < 1.6
    assert a["surface_c"]["lid"] < a["air_c"] < a["board_c"]
    assert a["surface_c"]["lid"] < a["board_env_c"] < a["air_c"]           # the board sees the air and the cooler walls


def test_metal_box_is_one_temperature():
    s = boxheat.solve(_box(material="aluminium (painted)"), 1.0, 25)
    assert max(s["surface_c"].values()) - min(s["surface_c"].values()) < 0.3
    plastic = boxheat.solve(_box(), 1.0, 25)["surface_c"]
    assert plastic["base"] - plastic["lid"] > 3                           # plastic: the base on the desk runs warm
    assert s["air_c"] < boxheat.solve(_box(), 1.0, 25)["air_c"]


def test_printed_box_is_found_round_the_board():
    spec = design.normalise(_spec())
    box = boxheat.geometry(spec, _geo(spec), None)
    assert box["kind"] == "suggested"
    x0, y0, x1, y1 = box["outer"]
    assert x0 < 0 and y0 < 0 and x1 > 30 and y1 > 20          # the board is 30 × 20 mm
    assert box["z0"] < -1.6 < 0 < box["z1"]                  # floor below the board, lid above
    enc = {**boxheat.enclosure.suggest(boxheat.enclosure.context(_geo(spec), spec)),
           "openings": [{"ref": "J1", "wall": "left"}], "headroom_mm": 12, "vents": {"lid": True, "sides": True}}
    vented = boxheat.geometry(spec, _geo(spec), enc)
    assert vented["kind"] == "printed" and vented["lid_slots"] and any(not s.get("name") for s in vented["side"])
    j1 = next(s for s in vented["side"] if s.get("name") == "J1")
    assert j1["wall"] == "left" and 0 < j1["open"] < 1 and j1["a"] < 10 < j1["b"]     # only the gap round it is open
    for r in vented["lid_slots"]:
        assert x0 <= r[0] < r[2] <= x1 and y0 <= r[1] < r[3] <= y1


def test_stock_case_box():
    spec = _spec()
    spec["board"]["case"] = {"name": "Hammond 1591", "inner_mm": [60, 40, 25], "standoff_mm": 5, "wall_mm": 2.5,
                             "material": "aluminium"}
    spec = design.normalise(spec)
    box = boxheat.geometry(spec, _geo(spec), None)
    assert box["kind"] == "case" and box["material"] == "aluminium"
    assert box["outer"] == [-17.5, -12.5, 47.5, 32.5]          # centred: (60 - 30) / 2 and (40 - 20) / 2, plus the wall
    assert box["z0"] == pytest.approx(-6.6 - 2.5) and box["z1"] == pytest.approx(25 + 2.5 - 6.6)


def test_whatif_runs_the_circuit_in_the_box_air(monkeypatch, tmp_path):
    monkeypatch.setattr(simlab.sim, "_symbol_pins", lambda lib: [])
    spec = design.normalise(_spec())
    seen = []

    def fake_sim(s, out_dir, **kw):
        seen.append(s["simulation"].get("ambient_c"))
        return {**_res(), "ambient_c": s["simulation"].get("ambient_c") or 25}
    monkeypatch.setattr(simlab.sim, "simulate", fake_sim)
    box = _box()
    d = simlab.whatif(spec, _res(), {"box": 1}, tmp_path, None, box)
    bh = d["box_heat"]
    assert bh["on"] and seen == [pytest.approx(bh["board_env_c"], abs=0.1)]     # one run, in what the board sees
    assert d["ambient_c"] == pytest.approx(bh["board_env_c"], abs=0.1) and bh["room_c"] == 25
    assert 25 < bh["board_env_c"] < bh["air_c"] < bh["board_c"]
    assert next(p for p in d["params"] if p["id"] == "ambient")["value"] == 25     # the slider stays the room
    rk = next(r for r in d["risks"] if r["source"] == "Enclosure")
    assert "the air reaches" in rk["text"] and "vents" in rk["params"]
    seen.clear()
    simlab.whatif(spec, _res(), {"box": 1, "r:R1": 47}, tmp_path, None, box)
    assert len(seen) == 2 and seen[0] in (None, 25) and seen[1] > 25     # room air for the heat, then inside
    seen.clear()
    off = simlab.whatif(spec, _res(), {"box": 0}, tmp_path, None, box)
    assert not off["box_heat"]["on"] and len(seen) == 1
    assert any("would reach" in r["text"] for r in off["risks"])
    same = simlab.apply(spec, _res(), {"box": 1, "vents": "both"})          # box controls never touch the design
    assert same["components"] == spec["components"] and not same["simulation"]


def test_box_report_warns_when_hot(monkeypatch):
    monkeypatch.setattr(simlab.sim, "_symbol_pins", lambda lib: [])
    res = _res()
    res["parts"][2]["current_ma"] = 600         # U1: 3 W in a small box
    rep = simlab.box_report(_spec(), res, _box())
    assert rep["air_c"] > 60 and rep["with_vents_c"] < rep["air_c"]
    assert any("warms the air" in w and "vents would bring it" in w for w in rep["warnings"])
    assert any("lid reaches" in w for w in rep["warnings"])
