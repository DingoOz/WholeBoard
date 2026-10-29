import pytest

from wholeboard import ngspice, sim


def comp(ref, value, footprint="", symbol="", **kw):
    return {"ref": ref, "value": value, "footprint": footprint, "symbol": symbol, **kw}


def spec(components, nets, simulation=None):
    s = {"name": "t", "board": {"width_mm": 30, "height_mm": 30}, "components": components, "nets": nets}
    if simulation:
        s["simulation"] = simulation
    return s


def test_values_and_names():
    assert sim.parse_value("4k7") == pytest.approx(4700)
    assert sim.parse_value("100nF") == pytest.approx(1e-7)
    assert sim.parse_value("470R") == 470
    assert sim.parse_value("2.2uH") == pytest.approx(2.2e-6)
    assert sim.parse_value("1M") == 1e6
    assert sim.net_volts("+3V3") == 3.3
    assert sim.net_volts("+5V") == 5
    assert sim.net_volts("VBUS") == 5
    assert sim.net_volts("VCC_12V") == 12
    assert sim.net_volts("SDA") is None
    f = sim.facts("-30℃~+85℃ 1.6V~2.6V 130° 195mcd 25mA 615nm~630nm 630nm 65mW Discrete Diode Red 0805 LED")
    assert f["v_range"] == (1.6, 2.6) and 0.025 in f["amps"] and f["watts"] == [0.065] and f["nm"] == 630


def test_classify():
    def k(c):
        p = sim.Part(c, {"1": "A", "2": "B"}, {}, {}, {}, {})
        return sim.classify(p)
    assert k(comp("R1", "10k", "Resistor_SMD:R_0603_1608Metric")) == "resistor"
    assert k(comp("D1", "RED", "LED_SMD:LED_0603_1608Metric", "Device:LED")) == "led"
    assert k(comp("U1", "AMS1117-3.3", symbol="Regulator_Linear:AMS1117-3.3")) == "ldo"
    assert k(comp("U2", "NE555", symbol="Timer:NE555D")) == "timer555"
    assert k(comp("U3", "ESP32-C3-MINI-1")) == "ic"
    assert k(comp("SW1", "slide", symbol="Switch:SW_SPDT")) == "switch"
    assert k(comp("J1", "USB-C", "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12")) == "connector"


def test_netlist_guesses_supply():
    s = spec([comp("J1", "9V battery snap", "Connector:Conn_01x02", description="9V battery"),
              comp("R1", "1k", "Resistor_SMD:R_0603_1608Metric"),
              comp("D1", "RED", "LED_SMD:LED_0603_1608Metric", "Device:LED")],
             [{"name": "+9V", "class": "power", "pins": ["J1.1", "R1.1"]},
              {"name": "A", "pins": ["R1.2", "D1.2"]},
              {"name": "GND", "class": "power", "pins": ["J1.2", "D1.1"]}])
    nl = sim.Netlist(s, symbol_pins=lambda _: [])
    text = nl.build()
    assert nl.sources and nl.sources[0]["volts"] == 9 and nl.sources[0]["kind"] == "battery"
    assert "D_D1 n" in text and "R_R1" in text


engine = ngspice.find()
needs_spice = pytest.mark.skipif(engine is None, reason="ngspice not available")


@needs_spice
def test_led_overcurrent_and_battery_life(tmp_path):
    s = spec([comp("R1", "47R", "Resistor_SMD:R_0603_1608Metric"),
              comp("D1", "RED", "LED_SMD:LED_0603_1608Metric", "Device:LED")],
             [{"name": "+5V", "class": "power", "pins": ["R1.1"]}, {"name": "A", "pins": ["R1.2", "D1.2"]},
              {"name": "GND", "class": "power", "pins": ["D1.1"]}],
             {"sources": [{"net": "+5V", "volts": 5, "kind": "battery", "capacity_mah": 1000}],
              "expect": [{"ref": "D1", "measure": "current", "max": 20}]})
    r = sim.simulate(s, tmp_path, engine=engine)
    assert r["ran"] and not r["ok"]
    assert any("D1 LED carries" in p for p in r["problems"])
    assert any("R1" in p and "mW" in p for p in r["problems"] + r["warnings"])
    assert r["supplies"][0]["battery_hours"] < 20


@needs_spice
def test_ldo_dropout_and_esp32_peak(tmp_path):
    comps = [comp("J1", "LiPo", "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", description="LiPo battery 1S"),
             comp("U1", "XC6206P332MR", "Package_TO_SOT_SMD:SOT-23", "Regulator_Linear:XC6206PxxxMR"),
             comp("U2", "ESP32-C3-MINI-1", "RF_Module:ESP32-C3-MINI-1"),
             comp("C1", "10uF", "Capacitor_SMD:C_0805_2012Metric")]
    nets = [{"name": "VBAT", "class": "power", "pins": ["J1.1", "U1.3"]},
            {"name": "+3V3", "class": "power", "pins": ["U1.2", "U2.3", "C1.1"]},
            {"name": "GND", "class": "power", "pins": ["J1.2", "U1.1", "U2.1", "C1.2"]}]
    r = sim.simulate(spec(comps, nets, {"sources": [{"net": "VBAT", "volts": 3.4, "kind": "battery"}]}),
                     tmp_path, engine=engine)
    assert r["ran"], r
    text = " ".join(r["problems"])
    assert "U1 is in dropout" in text
    assert "U1 must supply" in text and "peak" in text


@needs_spice
def test_555_frequency_expectation(tmp_path):
    comps = [comp("U1", "NE555", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Timer:NE555D"),
             comp("R1", "10k", "Resistor_SMD:R_0603_1608Metric"), comp("R2", "68k", "Resistor_SMD:R_0603_1608Metric"),
             comp("C1", "1uF", "Capacitor_SMD:C_0603_1608Metric"), comp("C2", "10nF", "Capacitor_SMD:C_0603_1608Metric")]
    nets = [{"name": "+5V", "class": "power", "pins": ["U1.8", "U1.4", "R1.1"]},
            {"name": "DIS", "pins": ["U1.7", "R1.2", "R2.1"]},
            {"name": "THR", "pins": ["U1.6", "U1.2", "R2.2", "C1.1"]},
            {"name": "CV", "pins": ["U1.5", "C2.1"]},
            {"name": "OUT", "pins": ["U1.3"]},
            {"name": "GND", "class": "power", "pins": ["U1.1", "C1.2", "C2.2"]}]
    f = 1.44 / ((10e3 + 2 * 68e3) * 1e-6)
    s = spec(comps, nets, {"sources": [{"net": "+5V", "volts": 5}],
                           "expect": [{"net": "OUT", "measure": "frequency", "min": f * 0.9, "max": f * 1.1}]})
    r = sim.simulate(s, tmp_path, engine=engine)
    assert r["ok"], r["problems"]
    assert r["expectations"][0]["ok"]


@needs_spice
def test_opamp_gain_ac(tmp_path):
    comps = [comp("U1", "MCP6001", "Package_TO_SOT_SMD:SOT-23-5", "Amplifier_Operational:MCP6001-OT"),
             comp("R1", "10k", "Resistor_SMD:R_0603_1608Metric"), comp("R2", "100k", "Resistor_SMD:R_0603_1608Metric")]
    nets = [{"name": "+5V", "class": "power", "pins": ["U1.5"]},
            {"name": "IN", "pins": ["U1.3"]},
            {"name": "FB", "pins": ["U1.4", "R1.1", "R2.1"]},
            {"name": "OUT", "pins": ["U1.1", "R2.2"]},
            {"name": "VMID", "pins": ["R1.2"]},
            {"name": "GND", "class": "power", "pins": ["U1.2"]}]
    s = spec(comps, nets, {"sources": [{"net": "+5V", "volts": 5},
                                       {"net": "VMID", "volts": 1.0, "kind": "bench"},
                                       {"net": "IN", "kind": "signal", "ac": 1,
                                        "wave": {"type": "sine", "amplitude": 0.05, "offset": 1.0, "freq_hz": 1000}}],
                           "ac": {"from_hz": 10, "to_hz": 1e6},
                           "expect": [{"net": "OUT", "measure": "gain_db", "freq_hz": 1000, "min": 20.0, "max": 21.6},
                                      {"net": "OUT", "measure": "cutoff_hz", "min": 50e3, "max": 200e3}]})
    r = sim.simulate(s, tmp_path, engine=engine)
    assert r["ok"], (r["problems"], r["expectations"])


@needs_spice
def test_an_ic_current_runs_in_its_supply_nets_only(tmp_path):
    s = spec([comp("U1", "MYCHIP", sim={"load_ma": 100}), comp("R1", "10k", "Resistor_SMD:R_0603_1608Metric")],
             [{"name": "+3V3", "class": "power", "pins": ["U1.1"]}, {"name": "+1V8", "class": "power", "pins": ["U1.2"]},
              {"name": "SIG", "pins": ["U1.3", "R1.1"]}, {"name": "GND", "class": "power", "pins": ["U1.4", "R1.2"]}],
             {"sources": [{"net": "+3V3", "volts": 3.3}, {"net": "+1V8", "volts": 1.8}]})
    r = sim.simulate(s, tmp_path, engine=engine)
    i = r["net_current_ma"]
    assert (i.get("SIG") or 0) < 1                         # not the chip's 100 mA
    assert i["+1V8"] >= 50 and i["+3V3"] >= 50              # each supply carries its share
