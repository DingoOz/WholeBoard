import json
from pathlib import Path

import pytest

from wholeboard import enclosure


def test_classify_heights():
    usb = enclosure.classify("Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", False)
    assert usb["kind"] == "port" and usb["height"] == pytest.approx(3.3)
    assert enclosure.classify("Capacitor_SMD:CP_Elec_6.3x7.7", False)["height"] == pytest.approx(8.0)
    assert enclosure.classify("LED_SMD:LED_0805_2012Metric", False)["kind"] == "led"
    assert enclosure.classify("MountingHole:MountingHole_3.2mm_M3", False)["kind"] == "none"
    own = enclosure.classify("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", False, 4.2)
    assert own["height"] == 4.2 and not own["estimated"]


def _geo():
    """A 40 x 30 mm board at (100, 100) with four M3 holes, a USB-C on the left edge and an LED."""
    fps, pads = [], []
    for i, (x, y) in enumerate(((103.5, 103.5), (136.5, 103.5), (103.5, 126.5), (136.5, 126.5)), 1):
        fps.append({"ref": f"H{i}", "lib": "MountingHole:MountingHole_3.2mm_M3", "x": x, "y": y,
                    "rot": 0, "side": "F.Cu", "court": [x - 1.85, y - 1.85, x + 1.85, y + 1.85]})
        pads.append({"ref": f"H{i}", "kind": "np_thru_hole", "drill": 3.2, "x": x, "y": y})
    fps.append({"ref": "J1", "lib": "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", "x": 103, "y": 115,
                "rot": 90, "side": "F.Cu", "court": [99.5, 110.5, 107.5, 119.5]})
    fps.append({"ref": "D1", "lib": "LED_SMD:LED_0805_2012Metric", "x": 125, "y": 115,
                "rot": 0, "side": "F.Cu", "court": [123.3, 114.0, 126.7, 116.0]})
    return {"bbox": [100, 100, 140, 130], "footprints": fps, "pads": pads}


SPEC = {"name": "t", "board": {"thickness_mm": 1.6, "corner_radius_mm": 1}, "components": []}


def test_context_and_suggestion():
    ctx = enclosure.context(_geo(), SPEC)
    assert ctx["board"]["width_mm"] == 40 and len(ctx["mounting_holes"]) == 4
    j1 = next(p for p in ctx["parts"] if p["ref"] == "J1")
    assert j1["nearest_wall"] == "left" and j1["kind"] == "port"
    enc = enclosure.suggest(ctx)
    assert enc["openings"] == [{"ref": "J1"}] and enc["board_fixing"] == "screws"
    assert enc["lid_holes"][0]["ref"] == "D1"
    assert "_extra" not in json.dumps(enclosure.public_context(ctx))


def test_validate_catches_mistakes():
    ctx = enclosure.context(_geo(), SPEC)
    errs, _ = enclosure.validate({"wall_mm": 20, "lid": {"type": "glue"}, "openings": [{"ref": "J9"}],
                                  "lid_holes": [{"shape": "rect", "x_mm": 1, "y_mm": 1}]}, ctx)
    text = " ".join(errs)
    assert "wall_mm" in text and "lid.type" in text and "J9" in text and "rect hole" in text


def test_resolve_geometry():
    ctx = enclosure.context(_geo(), SPEC)
    job = enclosure.resolve(ctx, enclosure.suggest(ctx))
    # The USB-C overhangs the left edge by 0.5 mm, so the cavity is 40.5 + 2 x 0.6 wide.
    assert job["outer"][0] == pytest.approx(40.5 + 1.2 + 4, abs=0.01)
    assert len(job["standoffs"]) == 4 and not job["ledges"]
    op = job["openings"][0]
    assert op["wall"] == "left" and op["u1"] - op["u0"] >= 12.5
    assert job["base_h"] > job["z_top"] + 3.3
    screws = enclosure.resolve(ctx, {**enclosure.suggest(ctx), "lid": {"type": "screws"}})
    assert len(screws["lid"]["columns"]) == 4 and screws["outer"][0] > job["outer"][0]


@pytest.mark.skipif(enclosure.find_freecad() is None, reason="FreeCAD is not installed")
def test_freecad_build(tmp_path):
    ctx = enclosure.context(_geo(), SPEC)
    s = enclosure.build(_geo(), SPEC, enclosure.suggest(ctx), tmp_path, "t", log=lambda m: None)
    assert s["ok"] and not s["collisions"]
    for k in ("base_stl", "lid_stl", "base_step", "freecad"):
        assert Path(s["files"][k]).stat().st_size > 1000
    preview = json.loads(Path(s["preview"]).read_text())
    assert {m["group"] for m in preview["meshes"]} == {"base", "lid", "board"}


@pytest.mark.skipif(enclosure.find_freecad() is None, reason="FreeCAD is not installed")
def test_the_lid_label_keeps_clear_of_the_led_window(tmp_path):
    """Centred, BLINKER would run over D1's window 5 mm right of the centre: it moves to the nearest clear spot,
    and the notes say why. Set where it is already clear, it stays put."""
    ctx = enclosure.context(_geo(), SPEC)
    enc = {**enclosure.suggest(ctx), "lid_holes": [{"ref": "D1", "diameter_mm": 3}],
           "label": {"text": "BLINKER", "size_mm": 6}}
    s = enclosure.build(_geo(), SPEC, enc, tmp_path / "a", "t", log=lambda m: None)
    assert s["ok"] and s["label"]["moved_mm"] > 1 and s["label"]["size"] == 6
    assert any("the lid hole for D1" in n and "moved" in n for n in s["freecad_notes"])
    clear = {**enc, "label": {"text": "HI", "size_mm": 4, "x_mm": 10, "y_mm": 8}}
    s = enclosure.build(_geo(), SPEC, clear, tmp_path / "b", "t", log=lambda m: None)
    assert s["label"]["moved_mm"] == 0 and not any("label" in n for n in s["freecad_notes"])
