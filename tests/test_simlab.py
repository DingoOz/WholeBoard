"""Sim Lab: the board outline, current-flow directions, LED light, sliders, risks and the scene."""

import math

import pytest

from wholeboard import design, simlab


def _spec():
    return {"name": "lab", "board": {"width_mm": 30, "height_mm": 20},
            "components": [
                {"ref": "J1", "value": "PWR", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"},
                {"ref": "R1", "value": "100R", "footprint": "Resistor_SMD:R_0603_1608Metric", "description": "D1 red"},
                {"ref": "R2", "value": "220R", "footprint": "Resistor_SMD:R_0603_1608Metric", "description": "D1 green"},
                {"ref": "D1", "value": "RGB", "footprint": "LED_SMD:LED_RGB_PLCC-4", "description": "RGB LED common anode"},
                {"ref": "U1", "value": "MCU", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"}],
            "nets": [{"name": "+5V", "class": "power", "pins": ["J1.1", "D1.2", "U1.8"]},
                     {"name": "GND", "class": "power", "pins": ["J1.2", "U1.4"]},
                     {"name": "LR", "pins": ["D1.1", "R1.1"]}, {"name": "LG", "pins": ["D1.3", "R2.1"]},
                     {"name": "PR", "pins": ["R1.2", "U1.1"]}, {"name": "PG", "pins": ["R2.2", "U1.2"]}]}


def _res(r1_ma=12.0, r1_mw=14.4):
    return {"ran": True, "ambient_c": 25, "problems": [], "warnings": ["U1 runs warm"],
            "supplies": [{"net": "+5V", "kind": "bench", "volts": 5.0, "current_ma": 30, "peak_ma": 40, "max_ma": 500}],
            "rails": [], "net_current_ma": {"+5V": 30, "GND": 40, "LR": r1_ma},
            "parts": [{"ref": "R1", "kind": "resistor", "value": "100R", "current_ma": r1_ma, "power_mw": r1_mw, "rating_mw": 100},
                      {"ref": "R2", "kind": "resistor", "value": "220R", "current_ma": 8.0, "power_mw": 14.1, "rating_mw": 100},
                      {"ref": "U1", "kind": "ic", "value": "MCU", "current_ma": 10.0, "peak_ma": 20, "supply_v": 5.0}]}


def test_outline_chains_lines_and_arcs_into_one_polygon():
    geo = {"outline": [{"kind": "line", "start": [1, 0], "end": [10, 0]},
                       {"kind": "line", "start": [10, 10], "end": [0, 10]},      # out of order and reversed
                       {"kind": "line", "start": [10, 0], "end": [10, 10]},
                       {"kind": "line", "start": [0, 10], "end": [0, 1]},
                       {"kind": "arc", "start": [0, 1], "mid": [1 - math.sqrt(0.5), 1 - math.sqrt(0.5)], "end": [1, 0]}]}
    poly = simlab.outline_polygon(geo)
    assert poly[0] == [1, 0] and [10, 10] in poly and [0, 10] in poly
    corner = [p for p in poly if p[0] < 1 and p[1] < 1]
    assert corner and all(abs(math.dist(p, [1, 1]) - 1) < 1e-3 for p in corner)     # the arc is sampled round
    assert simlab.outline_polygon({"outline": [{"kind": "rect", "start": [0, 0], "end": [5, 4]}]}) == \
        [[0, 0], [5, 0], [5, 4], [0, 4]]


def test_current_flows_away_from_the_source_and_back_on_ground():
    geo = {"tracks": [{"a": [10, 0], "b": [5, 0], "w": 0.3, "layer": "F.Cu", "net": "+5V"},
                      {"a": [0, 0], "b": [5, 0], "w": 0.3, "layer": "F.Cu", "net": "+5V"},
                      {"a": [0, 5], "b": [10, 5], "w": 0.3, "layer": "F.Cu", "net": "GND"}]}
    roots = {"+5V": [(-0.5, -0.5, 0.5, 0.5)], "GND": [(-0.5, 4.5, 0.5, 5.5)]}
    t = simlab.flows(geo, roots, {"GND"})
    assert t[1]["dir"] == 1       # drawn from the source outwards: a → b
    assert t[0]["dir"] == -1      # drawn towards the source, so it runs b → a
    assert t[2]["dir"] == -1      # ground current returns to the source


def test_rgb_led_channels_come_from_their_series_resistors(monkeypatch):
    monkeypatch.setattr(simlab.sim, "_symbol_pins", lambda lib: [])
    st = simlab.part_state(design.normalise(_spec()), _res())
    light = st["D1"]["light"]
    assert sorted(ch["ma"] for ch in light) == [8.0, 12.0]
    assert {tuple(ch["color"]) for ch in light} == {tuple(simlab.LED_COLORS["red"]), tuple(simlab.LED_COLORS["green"])}
    assert st["D1"]["power_mw"] == pytest.approx(2.0 * 12 + 3.0 * 8)
    assert st["U1"]["power_mw"] == pytest.approx(50.0)
    assert st["R1"]["power_mw"] == pytest.approx(14.4)


def test_sliders_and_applying_them():
    spec, res = design.normalise(_spec()), _res()
    ps = {p["id"]: p for p in simlab.params(spec, res)}
    assert {"ambient", "airflow", "copper", "src:+5V", "r:R1", "r:R2", "load:U1"} <= set(ps)
    assert ps["r:R1"]["value"] == 100 and ps["r:R1"]["log"] and ps["airflow"]["local"]
    out = simlab.apply(spec, res, {"r:R1": 47, "src:+5V": 4.5, "load:U1": 30, "ambient": 50})
    comps = {c["ref"]: c for c in out["components"]}
    assert comps["R1"]["value"] == "47R"
    assert comps["U1"]["sim"]["load_ma"] == 30 and comps["U1"]["sim"]["peak_ma"] == 30
    assert out["simulation"]["sources"] == [{"net": "+5V", "volts": 4.5, "kind": "bench", "max_ma": 500}]
    assert out["simulation"]["ambient_c"] == 50
    assert spec["components"][1]["value"] == "100R"          # the original is untouched
    assert simlab._ohms(4700) == "4.7k" and simlab._ohms(1.2e6) == "1.2M"


def test_risks_link_parts_to_their_sliders(monkeypatch):
    monkeypatch.setattr(simlab.sim, "_symbol_pins", lambda lib: [])
    spec = design.normalise(_spec())
    res = _res(r1_ma=26, r1_mw=68)
    d = simlab.dynamic(spec, res, {"problems": [], "warnings": ["C9 is far from U1"]})
    risks = d["risks"]
    assert risks[0]["level"] == "problem" and "D1 red carries 26.0 mA" in risks[0]["text"]
    assert set(risks[0]["params"]) >= {"r:R1", "r:R2"}               # the LED's series resistors
    assert [r["level"] for r in risks] == sorted((r["level"] for r in risks), key=["problem", "warning", "watch"].index)
    warm = next(r for r in risks if r["text"] == "U1 runs warm")
    assert warm["refs"] == ["U1"] and "load:U1" in warm["params"]
    assert any(r["source"] == "Board check" for r in risks)
    assert any(r["level"] == "watch" and "+5V supplies 40 mA" in r["text"] for r in risks)
    assert "r:R1" in d["links"]["D1"]


def test_scene_has_everything_the_viewer_draws(monkeypatch):
    monkeypatch.setattr(simlab.sim, "_symbol_pins", lambda lib: [])
    spec = _spec()
    fp = lambda ref, lib, x, y: {"ref": ref, "lib": lib, "x": x, "y": y, "rot": 0, "side": "F.Cu",
                                  "court": [x - 1, y - 1, x + 1, y + 1]}
    geo = {"bbox": [100, 50, 130, 70],
           "outline": [{"kind": "rect", "start": [100, 50], "end": [130, 70]}],
           "footprints": [fp("J1", spec["components"][0]["footprint"], 103, 60), fp("R1", spec["components"][1]["footprint"], 110, 55),
                          fp("R2", spec["components"][2]["footprint"], 110, 65), fp("D1", spec["components"][3]["footprint"], 118, 60),
                          fp("U1", spec["components"][4]["footprint"], 124, 60)],
           "pads": [{"ref": "J1", "name": "1", "x": 103, "y": 59, "w": 1.7, "h": 1.7, "rot": 0, "shape": "rect",
                     "kind": "thru_hole", "drill": 1.0, "front": True, "back": True, "net": "+5V"}],
           "tracks": [{"a": [103, 59], "b": [118, 59], "w": 0.5, "layer": "F.Cu", "net": "+5V"}],
           "vias": [], "zones": []}
    sc = simlab.scene(spec, geo, _res(), None)
    assert sc["board"] == {"w": 30, "h": 20, "t": 1.6, "layers": 2}
    assert sc["outline"] == [[0, 0], [30, 0], [30, 20], [0, 20]]
    assert {p["ref"] for p in sc["parts"]} == {"J1", "R1", "R2", "D1", "U1"}
    assert sc["tracks"][0]["a"] == [3, 9] and sc["tracks"][0]["dir"] == 1
    assert sc["net_kind"]["GND"] == "ground" and sc["net_kind"]["+5V"] == "power" and sc["net_kind"]["LR"] == "signal"
    assert sc["state"]["D1"]["light"] and sc["params"] and sc["risks"]
