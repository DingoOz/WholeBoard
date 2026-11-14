"""Antenna placement: vendor keep-outs, the board and enclosure checks, the pi network, the bring-up step, the
VNA measurements and the openEMS model."""

import json
import os

import pytest

from wholeboard import antenna, antenna_sim, blocks, bringup, enclosure, pipeline
from wholeboard.kicad import libs
from wholeboard.project import Project

HAVE_LIBS = bool(libs.search_footprints("R_0603", 1))
needs_libs = pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
C3 = "RF_Module:ESP32-C3-WROOM-02"


def _board(fps, w=60.0, h=40.0, x0=100.0, y0=100.0, **extra):
    """A w x h board at (x0, y0) with the given footprints (courtyards from the library)."""
    out = []
    for f in fps:
        f = {"rot": 0.0, "side": "F.Cu", **f}
        if "court" not in f:
            fp = libs.load_footprint(f["lib"])
            c = fp.courtyard
            pt = antenna._to_board(f["x"], f["y"], f["rot"], f["side"] == "B.Cu")
            f["court"] = list(antenna._rect_to_board(c, pt))
        out.append(f)
    return {"bbox": [x0, y0, x0 + w, y0 + h], "outline": [{"kind": "rect", "start": [x0, y0], "end": [x0 + w, y0 + h]}],
            "footprints": out, "pads": [], "tracks": [], "vias": [], "zones": [], **extra}


SPEC = {"name": "t", "board": {"width_mm": 60, "height_mm": 40}, "components": [{"ref": "U1", "footprint": C3, "value": "C3"}],
        "nets": [{"name": "GND", "pins": ["U1.1"]}]}


@needs_libs
def test_local_shape_from_the_footprint_keepout():
    s = antenna.local_shape(C3)
    assert s["antenna"] == pytest.approx((-9.0, -13.1, 9.0, -7.1))
    assert s["keepout"] == pytest.approx((-14.0, -18.1, 14.0, -7.1))
    assert s["facing"] == (0.0, -1.0)
    # Espressif's 15 mm clearance is drawn in KiCad's footprint: 15 mm beyond the module's body on each side
    e = antenna.local_shape("RF_Module:ESP32-S3-WROOM-1")
    assert e["keepout"][0] == pytest.approx(e["body"][0] - 15) and e["keepout"][1] == pytest.approx(e["body"][1] - 15)


@needs_libs
@pytest.mark.parametrize("rot,facing", [(0, (0, -1)), (90, (-1, 0)), (180, (0, 1)), (270, (1, 0))])
def test_rotation_turns_the_antenna(rot, facing):
    geo = _board([{"ref": "U1", "lib": C3, "x": 130, "y": 120, "rot": rot}])
    (a,) = antenna.find_antennas(SPEC, geo)
    assert a["facing"] == pytest.approx(facing)
    assert a["vendor"] == "Espressif" and a["plastic_mm"] == 15 and "15 mm" in a["sources"]["plastic"]
    assert "generic" in a["sources"]["metal"]


@needs_libs
def test_board_check_edge_copper_parts_and_ground():
    at_edge = _board([{"ref": "U1", "lib": C3, "x": 130, "y": 113}])
    r = antenna.check(SPEC, at_edge)
    row = r["antennas"][0]
    assert row["edge_mm"] <= 0 and not r["problems"]
    assert any("ground copper" in w for w in r["warnings"])          # no pour under the module
    inside = _board([{"ref": "U1", "lib": C3, "x": 130, "y": 125}],
                    tracks=[{"a": [125, 115], "b": [135, 115], "w": 0.2, "layer": "F.Cu", "net": "SDA"}],
                    zones=[{"layer": "B.Cu", "net": "GND", "pts": [[100, 100], [160, 100], [160, 140], [100, 140]]}])
    r = antenna.check(SPEC, inside)
    assert any("inside the board edge" in w for w in r["warnings"])
    assert any("copper under the antenna" in p and "SDA" in p and "GND pour" in p for p in r["problems"])
    assert not any("ground copper" in w for w in r["warnings"])
    battery = _board([{"ref": "U1", "lib": C3, "x": 130, "y": 113},
                      {"ref": "BT1", "lib": "Battery:BatteryHolder_Keystone_3034_1x20mm", "x": 140, "y": 103,
                       "court": [139, 100.5, 152, 112]}])
    r = antenna.check(SPEC, battery)
    assert any("BT1" in p and "keep-out" in p for p in r["problems"])


@needs_libs
def test_placement_warnings_before_layout():
    def warn(pl):
        c = {"ref": "U1", "footprint": C3, "value": "x", **({"placement": pl} if pl else {})}
        return antenna.placement_warnings({**SPEC, "components": [c]})
    assert "fix its placement" in warn(None)[0]
    assert warn({"x_mm": 30, "y_mm": 13, "rotation": 0}) == []
    assert "inside the board edge" in warn({"x_mm": 30, "y_mm": 13, "rotation": 180})[0]
    assert warn({"x_mm": 9, "y_mm": 20, "rotation": 90}) == []


@needs_libs
def test_pi_network_block_is_recognised():
    spec = {"name": "t", "board": {"width_mm": 40, "height_mm": 30},
            "components": [{"ref": "AE1", "value": "2450AT18A100", "footprint": "RF_Antenna:Johanson_2450AT18x100", "lcsc": "C1"}],
            "nets": [{"name": "ANT", "pins": ["AE1.1"]}, {"name": "GND", "pins": ["AE1.2"]}, {"name": "RF", "pins": []}]}
    assert not antenna.has_pi_match(spec, antenna.feed_net(spec, "AE1"))
    spec["blocks"] = [{"block": "antenna_pi_match", "name": "M1", "connect": {"RF_IN": "RF", "RF_ANT": "ANT"}}]
    s = blocks.expand(spec)
    assert antenna.feed_net(s, "AE1") == "ANT" and antenna.has_pi_match(s, "ANT")
    dnp = [c["ref"] for c in s["components"] if c.get("dnp")]
    assert dnp == ["C1", "C2"]
    assert {"nets": ["ANT"], "kind": "single", "profile": "rf_50"} in s["impedance"]


def _ant(**kw):
    return {"ref": "U1", "kind": "module", "antenna": (110, 100, 128, 106), "keepout": None, "body": None,
            "facing": (0.0, -1.0), "side": "top", "plastic_mm": 15.0, "metal_mm": 25.0,
            "sources": {"plastic": "vendor doc", "metal": "generic"}, **kw}


def test_box_check_walls_lid_and_metal():
    z = {"board": 4.0, "thick": 1.6, "lid": 20.0}
    r = antenna.box_check([_ant()], (98, 98, 170, 150), z, [{"what": "board screw", "x": 112, "y": 110, "r": 1.6}])
    row = r["rows"][0]
    assert row["distances"]["top wall"] == 2.0
    assert any("top wall it faces" in p for p in r["problems"])        # 2 mm from the wall it faces, needs 15
    assert any("board screw" in w for w in r["warnings"])
    assert any("lid" in w for w in r["warnings"])                     # 20 - 5.6 - 3.2 = 11.2 mm < 15
    alu = antenna.box_check([_ant()], (80, 80, 170, 150), {"board": 15, "thick": 1.6, "lid": 50}, [], "aluminium")
    assert alu["metal_case"] and any("u.FL-to-SMA bulkhead" in p for p in alu["problems"])


def test_enclosure_moves_the_wall_the_antenna_faces():
    ctx = {"board": {"width_mm": 40, "height_mm": 30, "thickness_mm": 1.6, "corner_radius_mm": 0},
           "mounting_holes": [], "parts": [],
           "antennas": [{"ref": "U1", "antenna_mm": [10, 0, 28, 6], "faces": "top", "clearance_mm": 15,
                         "metal_clearance_mm": 25}]}
    base = {"wall_mm": 2, "gap_mm": 0.6}
    plain = enclosure.resolve(ctx, {**base, "antenna_room": False})
    roomy = enclosure.resolve(ctx, base)
    assert roomy["cavity"][3] == pytest.approx(plain["cavity"][3] + 15 - 0.6)
    assert any("antenna" in n for n in roomy["summary"]["notes"])
    ants = [{**_ant(antenna=(110, 100, 128, 106)), "body": (110, 100, 128, 120)}]
    r = antenna.job_check(ants, roomy, (100, 100), 30)
    assert r["rows"][0]["distances"]["top wall"] == pytest.approx(15.0)


def test_bringup_step_and_measurements(tmp_path):
    ants = [{"ref": "U1", "kind": "module", "band_mhz": [2400, 2483.5]},
            {"ref": "J2", "kind": "connector", "band_mhz": [2400, 2483.5]}]
    g = bringup.guide({"name": "t", "board": {"width_mm": 10, "height_mm": 10}, "components": [], "nets": []},
                      None, None, None, ants, boxed=True)
    st = g["steps"][-1]
    assert st["title"] == "Antenna match (VNA)" and "NanoVNA" in st["items"][0]
    assert any("J2" in x for x in st["items"]) and any("cannot be re-tuned" in x for x in st["items"])
    assert "Antenna match (VNA)" in g["markdown"] and g["antennas"][0]["ref"] == "U1"

    p = Project(tmp_path, {"id": "x", "revision": 3, "visuals": {"outputs:bringup": {"title": "Bring-up guide", "steps": []}}})
    rows = pipeline.add_antenna_measurement(p, {"ref": "U1", "enclosed": True, "resonance_mhz": "2420", "s11_db": -14})
    assert rows[0]["revision"] == 3 and rows[0]["resonance_mhz"] == 2420
    assert p.state["visuals"]["outputs:bringup"]["measurements"] == rows
    with pytest.raises(ValueError):
        pipeline.add_antenna_measurement(p, {"ref": "U1", "s11_db": 5})
    with pytest.raises(ValueError):
        pipeline.add_antenna_measurement(p, {"ref": "U1"})


@needs_libs
def test_simulation_model_and_summary(monkeypatch, tmp_path):
    geo = _board([{"ref": "U1", "lib": C3, "x": 130, "y": 113}])
    box = {"cavity": [98, 98, 162, 142], "wall": 2, "floor": 1.6, "lid": 1.6, "z_floor": -5, "z_lid": 12, "material": "PETG"}
    m = antenna_sim.build_model(SPEC, geo, box=box)
    assert m["ref"] == "U1" and [v["name"] for v in m["variants"]] == ["board", "boxed"]
    assert m["variants"][1]["dielectrics"][0]["eps"] == antenna_sim.PLASTIC_EPS["PETG"]
    assert 10 < m["antenna_length_mm"] < 20 and m["port"]["dir"] == "y"
    # the feed sits in the antenna area, on top of the board
    a = m["port"]["a"]
    assert 121 <= a[0] <= 139 and -113 <= a[1] <= -105 and a[2] == 1.6
    json.dumps(m)                                                       # the solver script reads it as JSON
    res = {"variants": [{"title": "Bare board", "freq_mhz": [2300, 2440, 2600], "s11_db": [-3, -18, -4], "resonance_mhz": 2440, "s11_min_db": -18},
                        {"title": "In the box", "freq_mhz": [2300, 2440, 2600], "s11_db": [-12, -8, -2], "resonance_mhz": 2300, "s11_min_db": -12}]}
    s = antenna_sim.summarise(m, res)
    assert s["rows"][1]["worst_in_band_db"] == -8 and any("-140 MHz" in n for n in s["notes"])
    monkeypatch.setattr(antenna_sim, "command", lambda: None)
    assert antenna_sim.run(m, tmp_path)["available"] is False


@pytest.mark.skipif(not os.environ.get("WHOLEBOARD_OPENEMS_TEST"), reason="slow: set WHOLEBOARD_OPENEMS_TEST=1 with openEMS installed")
@needs_libs
def test_openems_solves_the_model(tmp_path):
    geo = _board([{"ref": "U1", "lib": C3, "x": 130, "y": 113}], w=40, h=30, x0=110)
    m = antenna_sim.build_model(SPEC, geo)
    r = antenna_sim.run(m, tmp_path)
    assert r["ok"], r.get("error")
    v = r["variants"][0]
    assert 1500 < v["resonance_mhz"] < 3500 and v["s11_min_db"] < -3
    assert 0 < v["pattern"]["directivity_dbi"] < 10              # a small antenna: a few dBi, not the -90 floor
