"""Where each net and part is on the schematic, for tracing nets in the schematic view."""
import itertools
import json
from pathlib import Path

import pytest

from wholeboard import sexpr
from wholeboard.kicad import cli, libs, schematic, schnets
from wholeboard.sexpr import S, Sym

SPEC = json.loads((Path(__file__).parent / "sample_spec.json").read_text())
HAVE_LIBS = libs.locate("footprints") is not None
HAVE_CLI = bool(cli.find_kicad_cli())


def _two_sheets():
    spec = json.loads(json.dumps(SPEC))
    reg = [c["ref"] for c in spec["components"] if schematic.stage(c) == 2]
    for c in spec["components"]:
        if c["ref"] in reg or c["ref"] in ("C1", "C2"):
            c["sheet"] = "Power"
    return spec


def _pins(data, net):
    return {(i["s"], i["ref"], i["pin"]): (i["x"], i["y"]) for i in data["nets"][net]["items"] if i["k"] == "pin"}


def test_label_box_runs_away_from_its_anchor():
    assert schnets.label_box(10, 10, 0, "GND", "label")[0] == 10       # text to the right
    assert schnets.label_box(10, 10, 180, "GND", "label")[2] == 10     # to the left
    assert schnets.label_box(10, 10, 90, "GND", "label")[3] == 10      # upwards
    assert schnets.label_box(10, 10, 270, "GND", "label")[1] == 10     # downwards
    assert schnets.bus_members("D[0..3]") == ["D0", "D1", "D2", "D3"]
    assert schnets.bus_members("I2C{SDA SCL}") == ["I2C.SDA", "I2C.SCL"]
    assert schnets.bus_members("SDA") == []


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_generated_sidecar_puts_labels_on_pins_across_sheets(tmp_path):
    spec = _two_sheets()
    sch = schematic.build(spec, tmp_path)
    data = sch["nets"]
    assert [s["title"] for s in data["sheets"]] == ["Main", "Power"]
    power = {n["name"] for n in spec["nets"] if n.get("class") == "power"}
    for n in spec["nets"]:
        items = data["nets"][n["name"]]["items"]
        pins = {(i["ref"], i["pin"]) for i in items if i["k"] == "pin"}
        assert pins == {tuple(p.split(".", 1)) for p in n["pins"]}, n["name"]
        labels = {(i["s"], i["x"], i["y"]) for i in items if i["k"].endswith("label")}
        for i in items:            # every pin has its label at its connection point
            if i["k"] == "pin":
                assert (i["s"], i["x"], i["y"]) in labels
                assert (i["x2"], i["y2"]) != (i["x"], i["y"])
        assert data["nets"][n["name"]]["power"] == (n["name"] in power)
    # a signal net shared by both sheets reaches the main sheet through the Power sheet's sheet pin
    shared = [n for n, v in data["nets"].items() if len({i["s"] for i in v["items"]}) > 1 and not v["power"]]
    for n in shared:
        assert any(i["k"] == "sheet_pin" and i["s"] == 0 for i in data["nets"][n]["items"])
        assert any(i["k"] == "hierarchical_label" and i["s"] == 1 for i in data["nets"][n]["items"])
    assert data["blocks"] and data["blocks"][0]["sheet"] == "Power"
    # every part has a box on the sheet it was put on, round its pins
    for ref, p in data["parts"].items():
        for it in p["items"]:
            x0, y0, x1, y1 = it["box"]
            assert x1 > x0 and y1 > y0
            for i in (i for v in data["nets"].values() for i in v["items"] if i.get("ref") == ref and i["s"] == it["s"]):
                assert x0 - 0.01 <= i["x2"] <= x1 + 0.01 and y0 - 0.01 <= i["y2"] <= y1 + 0.01


@pytest.mark.skipif(not (HAVE_LIBS and HAVE_CLI), reason="KiCad libraries or kicad-cli not installed")
def test_sidecar_matches_the_cropped_svg_and_reads_back_from_the_schematic(tmp_path):
    spec = _two_sheets()
    sch = schematic.build(spec, tmp_path)
    svg_dir = tmp_path / "svg"
    cli.run(["sch", "export", "svg", "--exclude-drawing-sheet", "-o", str(svg_dir), sch["sch"]], cwd=tmp_path)
    stem = Path(sch["sch"]).stem
    svgs = {}
    for pg in sch["pages"]:
        f = svg_dir / (f"{stem}-{pg['name']}.svg" if pg["name"] else f"{stem}.svg")
        schematic.crop_svg(f, pg["extent"])
        svgs[pg["name"]] = f
    data = schnets.finish(sch["nets"], svgs)
    for s, sh in enumerate(data["sheets"]):
        x, y, w, h = sh["viewBox"]
        pg = next(p for p in sch["pages"] if p["name"] == sh["name"])
        assert [x, y, x + w, y + h] == pytest.approx([pg["extent"][0] - 6, pg["extent"][1] - 6,
                                                      pg["extent"][2] + 6, pg["extent"][3] + 6], abs=1e-3)
        pts = [(i["x"], i["y"]) for v in data["nets"].values() for i in v["items"] if i["s"] == s]
        assert pts and all(x <= px <= x + w and y <= py <= y + h for px, py in pts)     # all on the picture
    # the reader for imported schematics finds the same pins at the same places from the .kicad_sch files
    pin_net = {p: n["name"] for n in spec["nets"] for p in n["pins"]}
    back = schnets.read(Path(sch["sch"]), pin_net)
    assert [s["name"] for s in back["sheets"]] == [s["name"] for s in data["sheets"]]
    for n in spec["nets"]:
        got = _pins(back, n["name"])
        want = _pins(data, n["name"])
        assert got.keys() == want.keys(), n["name"]
        for k in want:
            assert got[k] == pytest.approx(want[k], abs=1e-3)
    assert back["parts"].keys() == data["parts"].keys()


@pytest.mark.skipif(not (HAVE_LIBS and HAVE_CLI), reason="KiCad libraries or kicad-cli not installed")
def test_pin_positions_agree_with_kicad_for_every_angle_and_mirror(tmp_path):
    lib = schematic.load_symbol("Device:Q_NPN")
    pins = schnets._lib_pins(lib)
    body = schematic._header(schematic.uid(), "A3", 420, 297, "xf", [lib])
    expect = {}
    for i, (ang, mir) in enumerate(itertools.product((0, 90, 180, 270), (None, "x", "y"))):
        X, Y, ref = 30 + (i % 4) * 40, 30 + (i // 4) * 40, f"Q{i + 1}"
        inst = [Sym("symbol"), S(Sym("lib_id"), "Device:Q_NPN"), S(Sym("at"), X, Y, ang)]
        if mir:
            inst.append(S(Sym("mirror"), Sym(mir)))
        inst += [S(Sym("unit"), 1), [Sym("uuid"), schematic.uid()],
                 [Sym("property"), "Reference", ref, S(Sym("at"), X, Y, 0)],
                 [Sym("property"), "Value", "Q", S(Sym("at"), X, Y, 0)]]
        allp = pins.get(0, []) + pins.get(1, [])
        inst += [[Sym("pin"), p["number"], [Sym("uuid"), schematic.uid()]] for p in allp]
        body.append(inst)
        for p in allp:
            x, y = schnets._transform(p["x"], p["y"], (X, Y, ang), mir)
            body.append(schematic._label("label", f"N{ref}_{p['number']}", round(x, 4), round(y, 4), 0))
            expect[f"{ref}.{p['number']}"] = f"N{ref}_{p['number']}"
    body.append([Sym("sheet_instances"), [Sym("path"), "/", S(Sym("page"), "1")]])
    f = tmp_path / "xf.kicad_sch"
    f.write_text(sexpr.dumps(body) + "\n")
    cli.run(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(tmp_path / "xf.net"), str(f)], cwd=tmp_path)
    t = sexpr.parse((tmp_path / "xf.net").read_text())
    got = {f"{sexpr.find(nd, 'ref')[1]}.{sexpr.find(nd, 'pin')[1]}": str(sexpr.find(n, "name")[1]).lstrip("/")
           for n in sexpr.find_all(sexpr.find(t, "nets"), "net") for nd in sexpr.find_all(n, "node")}
    assert {k: got.get(k) for k in expect} == expect


@pytest.mark.skipif(not HAVE_LIBS, reason="KiCad libraries not installed")
def test_reader_follows_wires_power_symbols_and_buses(tmp_path):
    r, gnd = schematic.load_symbol("Device:R"), schematic.load_symbol("power:GND")
    pins = {p["number"]: p for p in schnets._lib_pins(r)[1]}

    def sym(lib_id, ref, x, y, value, power=False):
        return [Sym("symbol"), S(Sym("lib_id"), lib_id), S(Sym("at"), x, y, 0), S(Sym("unit"), 1),
                [Sym("uuid"), schematic.uid()], [Sym("property"), "Reference", ref, S(Sym("at"), x, y, 0)],
                [Sym("property"), "Value", value, S(Sym("at"), x, y, 0)]]

    def wire(*pts):
        return [Sym("wire"), [Sym("pts"), *[S(Sym("xy"), x, y) for x, y in pts]], [Sym("uuid"), schematic.uid()]]

    X, Y = 50.8, 50.8
    p1 = schnets._transform(pins["1"]["x"], pins["1"]["y"], (X, Y, 0), None)
    p2 = schnets._transform(pins["2"]["x"], pins["2"]["y"], (X, Y, 0), None)
    gnd_pin = schnets._lib_pins(gnd)
    gp = next(iter(gnd_pin.values()))[0]
    g_at = (p2[0] - gp["x"], p2[1] + 10.16 + gp["y"])      # the GND symbol's pin 10.16 mm below R1's pin 2
    body = schematic._header(schematic.uid(), "A4", 297, 210, "t", [r, gnd])
    body += [sym("Device:R", "R1", X, Y, "10k"), sym("power:GND", "#PWR01", g_at[0], g_at[1], "GND"),
             wire(p1, (p1[0], p1[1] - 10.16), (p1[0] + 20.32, p1[1] - 10.16)),        # R1.1 up and right ...
             schematic._label("label", "SIG", p1[0] + 10.16, p1[1] - 10.16, 0),       # ... named in its middle
             wire(p2, (p2[0], p2[1] + 10.16)),                                         # R1.2 down to GND
             [Sym("bus"), [Sym("pts"), S(Sym("xy"), 100, 30), S(Sym("xy"), 100, 80)], [Sym("uuid"), schematic.uid()]],
             schematic._label("label", "D[0..3]", 100, 40, 0),
             [Sym("bus_entry"), S(Sym("at"), 100, 60), S(Sym("size"), 2.54, 2.54), [Sym("uuid"), schematic.uid()]],
             wire((102.54, 62.54), (110, 62.54)), schematic._label("label", "D1", 110, 62.54, 0),
             [Sym("sheet_instances"), [Sym("path"), "/", S(Sym("page"), "1")]]]
    f = tmp_path / "t.kicad_sch"
    f.write_text(sexpr.dumps(body) + "\n")
    data = schnets.read(f, {"R1.1": "SIG"})
    sig = data["nets"]["SIG"]["items"]
    assert {i["k"] for i in sig} == {"pin", "wire", "label"} and sum(i["k"] == "wire" for i in sig) == 2
    g = data["nets"]["GND"]
    assert g["power"] and {i["k"] for i in g["items"]} >= {"pin", "power", "wire"}
    assert any(i["k"] == "pin" and i["ref"] == "R1" and i["pin"] == "2" for i in g["items"])
    d1 = {i["k"] for i in data["nets"]["D1"]["items"]}
    assert {"bus", "bus_label", "label", "wire"} <= d1      # the bus and its label count for each member
    assert "bus" in {i["k"] for i in data["nets"]["D3"]["items"]}
    assert "#PWR01" not in data["parts"] and data["parts"]["R1"]["value"] == "10k"


@pytest.mark.skipif(not (HAVE_LIBS and HAVE_CLI), reason="KiCad libraries or kicad-cli not installed")
def test_imported_project_view_has_every_sheet_and_the_importers_net_names(tmp_path):
    from wholeboard.kicad import importer
    proj = tmp_path / "proj"
    proj.mkdir()
    spec = _two_sheets()
    for c in spec["components"]:
        if c["ref"].startswith(("R", "D")):
            c["sheet"] = "Indicators"
    schematic.build(spec, proj)
    res = importer.to_spec(proj, log=lambda m: None)
    view = importer.schematic_view(proj, res["spec"], tmp_path / "view")
    assert [p["title"] for p in view["pages"]] == ["Main", "Indicators", "Power"]
    assert all(p["svg"].exists() and p["parts"] for p in view["pages"])
    data = json.loads(view["nets"].read_text())
    assert all(s["viewBox"] for s in data["sheets"])
    for n in res["spec"]["nets"]:                       # every net the importer made can be traced, at its pins
        pins = {f"{i['ref']}.{i['pin']}" for i in data["nets"][n["name"]]["items"] if i["k"] == "pin"}
        assert pins == set(n["pins"]), n["name"]
