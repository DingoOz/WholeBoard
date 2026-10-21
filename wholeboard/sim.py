"""Simulate a design spec with ngspice and check the circuit against its parts' ratings.

Every component gets a SPICE model chosen from its reference, symbol, value, part number and
JLCPCB description: resistors, capacitors, inductors, LEDs (forward voltage from the colour or the
datasheet range), diodes, zeners, TVS, BJTs, MOSFETs, slide switches, linear regulators (with
dropout, current limit and heat), switching regulators (as averaged power converters), 555
timers, op-amps and comparators. Other ICs become loads on their supply pins with typical and
peak currents (an ESP32 draws ~80 mA and peaks at ~350 mA during Wi-Fi transmit).

Supplies come from the spec's "simulation" section, or are guessed from connectors, batteries
and net names. The simulation runs an operating point (typical and peak loads), a transient
(start-up, oscillators, signals) and optionally an AC sweep, then reports:
  - rail voltages and supply currents, against the supply's rating, and battery life,
  - per-part current, voltage, power and temperature against the part's rating,
  - regulator dropout, input range and IC supply ranges,
  - oscillator frequency and duty cycle on every net that toggles,
  - the AI's own expectations ("OUT blinks at 0.8 to 1.2 Hz").
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import design, ngspice

SIM_DOC = """Simulation (optional "simulation" section of the design spec, and "sim" on components)

Every board is simulated with ngspice after the design check. Models are chosen automatically;
supplies are guessed from connectors, batteries and power net names. Say what you know:

"simulation": {
  "sources": [                       // what powers the board, and test signals
    {"net": "VBUS", "volts": 5, "kind": "usb", "max_ma": 500},
    {"net": "VBAT", "volts": 9, "kind": "battery", "capacity_mah": 500, "ohms": 1.5, "max_ma": 100},
    {"net": "IN", "kind": "signal", "wave": {"type": "sine", "amplitude": 0.5, "freq_hz": 1000, "offset": 1.65}},
    {"net": "CLK", "kind": "signal", "wave": {"type": "pulse", "low": 0, "high": 3.3, "freq_hz": 1000, "duty": 0.5}}
  ],                                 // kind: usb | battery | bench | signal; "ac": 1 makes it the AC sweep input
  "time_ms": 3000,                   // transient length (default: picked from the circuit)
  "ac": {"from_hz": 10, "to_hz": 1000000},   // frequency sweep; needs a source with "ac": 1
  "probes": ["OUT", "+3V3"],         // nets to plot (rails and toggling nets are plotted anyway)
  "expect": [                        // what the circuit should do; failures stop the build
    {"net": "OUT", "measure": "frequency", "min": 0.8, "max": 1.2},       // Hz
    {"net": "OUT", "measure": "duty", "min": 0.4, "max": 0.7},            // 0..1
    {"net": "+3V3", "measure": "voltage", "min": 3.2, "max": 3.4},        // settled average, V
    {"net": "VBUS", "measure": "ripple", "max": 0.05},                    // peak-to-peak, V
    {"ref": "D1", "measure": "current", "min": 2, "max": 15},             // mA (LEDs, resistors, diodes...)
    {"net": "OUT", "measure": "gain_db", "freq_hz": 1000, "min": 19, "max": 21},   // AC sweep
    {"net": "OUT", "measure": "cutoff_hz", "min": 900, "max": 1100}                // -3 dB point
  ],
  "ambient_c": 25,
  "battery_profile": {"active_pct": 5, "sleep_ma": 0.05},   // duty cycle for battery life (optional)
  "blocking": true                   // false: report problems but keep building
}

Per component "sim" (only when the automatic model is wrong):
  {"skip": true}                                   leave the part out
  {"vf": 2.0, "max_ma": 20}                        LED forward voltage and current limit
  {"vout": 3.3, "dropout": 0.25, "max_ma": 300}    regulator; "vref": 0.8 for adjustable/switching ones
  {"load_ma": 25, "peak_ma": 120}                  IC supply current (typical, peak)
  {"pins": {"PA5": "high", "PB2": "low", "7": 1.8}} IC pins driven high (its supply), low, or to a voltage
  {"closed": [["1", "2"]]}                         switch contacts that are closed (pushbuttons are open
                                                   by default, slide/toggle switches take the first throw)
  {"vth": 1.0}                                     MOSFET threshold
  {"spice": "R{ref} {1} {2} 4.7k"}                 your own SPICE lines: {PAD} is the node of that pad,
                                                   {ref} the reference, {gnd} ground; several lines with \\n
MCU pins that drive LEDs through a resistor are driven high (anode side) or low (cathode side)
automatically, so LED currents are checked. Results list every assumption the models made."""

VT = 0.02585
GND_RE = re.compile(r"^(A|D|P|S|C)?GND\w*$|^VSS\w*$|^0V$", re.I)
_MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3,
         "M": 1e6, "G": 1e9, "R": 1.0, "r": 1.0, "E": 1.0, "": 1.0}


# ---------------------------------------------------------------------------------- values
def parse_value(text) -> float | None:
    """'4k7' -> 4700, '100nF' -> 1e-7, '470R' -> 470, '2.2uH' -> 2.2e-6, '1M' -> 1e6."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text or "").strip().replace("Ω", "").replace("ohm", "").replace("Ohm", "").replace(" ", "")
    m = re.match(r"^(\d+)([pnuµμmkKMGRrE])(\d+)", s)
    if m:
        return float(f"{m[1]}.{m[3]}") * _MULT[m[2]]
    m = re.match(r"^(\d*\.?\d+)(?:[eE](-?\d+))?([pnuµμmkKMGRr]?)", s)
    if not m:
        return None
    v = float(m[1]) * (10 ** int(m[2]) if m[2] else 1)
    return v * _MULT[m[3]]


def net_volts(name: str) -> float | None:
    """Voltage implied by a net name: '+3V3' -> 3.3, '+5V' -> 5, 'VCC_12V' -> 12, 'VBUS' -> 5."""
    n = name.upper().lstrip("+")
    if n in ("VBUS", "USB_VBUS", "VUSB", "USB5V"):
        return 5.0
    m = re.search(r"(?:^|[_\-])?(\d+)V(\d+)?(?:$|[_\-])", n) or re.search(r"(\d+)V(\d*)$", n)
    if m:
        v = float(f"{m[1]}.{m[2]}") if m[2] else float(m[1])
        return v if 0.5 <= v <= 60 else None
    m = re.search(r"(\d+\.\d+)V", n)
    return float(m[1]) if m else None


def facts(text: str) -> dict:
    """Ratings in a JLCPCB/LCSC description: volts, amps, watts, LED Vf range, wavelength, dropout."""
    t = text or ""
    out: dict = {}
    vr = re.search(r"(\d+\.?\d*)\s*V\s*[~\-]\s*(\d+\.?\d*)\s*V", t)
    if vr:
        out["v_range"] = (float(vr[1]), float(vr[2]))
    out["volts"] = [float(x) for x in re.findall(r"(?<![\d.@~])(\d+\.?\d*)\s*V\b(?!@|\s*~)", t)]
    amps = []
    for v, unit in re.findall(r"(?<![\d.@])(\d+\.?\d*)\s*(mA|A|uA|µA)\b(?!@)", t):
        amps.append(float(v) * {"mA": 1e-3, "A": 1.0, "uA": 1e-6, "µA": 1e-6}[unit])
    out["amps"] = amps
    watts = []
    for v, unit in re.findall(r"(?<![\d.])(\d+\.?\d*|\d+/\d+)\s*(mW|W)\b", t):
        num = eval(v, {"__builtins__": {}}) if "/" in v else float(v)  # noqa: S307 - digits and '/' only
        watts.append(num * (1e-3 if unit == "mW" else 1.0))
    out["watts"] = watts
    nm = re.findall(r"(\d{3})\s*nm", t)
    if nm:
        out["nm"] = float(nm[-1])
    dv = re.search(r"(\d+\.?\d*)\s*(m?)V\s*@\s*\(?\s*(\d+\.?\d*)\s*(m?)A", t)
    if dv:
        out["v_at"] = (float(dv[1]) * (1e-3 if dv[2] else 1), float(dv[3]) * (1e-3 if dv[4] else 1))
    return out


def _clean_name(n: str) -> str:
    return re.sub(r"~\{|\}|[~/]", "", str(n or "")).strip().upper()


def _fmt(v: float) -> str:
    return f"{v:.6g}"


# ---------------------------------------------------------------------------------- tables
RES_RATING = {"0201": 0.05, "0402": 0.0625, "0603": 0.1, "0805": 0.125, "1206": 0.25, "1210": 0.5,
              "2010": 0.75, "2512": 1.0}
THETA_JA = [("SOT-223", 90), ("TO-252", 60), ("DPAK", 60), ("TO-263", 45), ("D2PAK", 45), ("TO-220", 60),
            ("SOT-89", 140), ("SOT-23-5", 210), ("SOT-23-6", 200), ("SOT-353", 300), ("SOT-363", 300),
            ("SOT-23", 250), ("SC-70", 300), ("SOT-323", 300), ("SOIC-8", 120), ("MSOP", 160),
            ("DFN", 70), ("QFN", 45), ("SOD-123", 250), ("SOD-323", 400), ("SOD-523", 500), ("SMA", 90),
            ("SMB", 75), ("SMC", 60), ("TO-92", 200)]

# (family regex, nominal vout or None, dropout V, max A, quiescent A, max input V)
LDO_FAMILIES = [
    (r"AMS1117|LM1117|LD1117|AZ1117|SPX1117|NCP1117", None, 1.1, 0.8, 5e-3, 15),
    (r"XC6206", None, 0.25, 0.2, 1e-6, 6), (r"XC6220|XC6210", None, 0.1, 0.7, 50e-6, 6),
    (r"AP2112", None, 0.25, 0.6, 55e-6, 6), (r"AP2125|AP7361", None, 0.3, 1.0, 70e-6, 6),
    (r"HT7[35]\d\d|HT75\d\d", None, 0.1, 0.25, 4e-6, 24), (r"MCP1700", None, 0.18, 0.25, 2e-6, 6),
    (r"MCP1702", None, 0.6, 0.25, 2e-6, 13.2), (r"MCP1703", None, 0.6, 0.25, 2e-6, 16),
    (r"ME6211", None, 0.12, 0.5, 40e-6, 6), (r"RT9013|RT9193", None, 0.25, 0.5, 25e-6, 5.5),
    (r"TLV755|TLV757|TLV758", None, 0.24, 0.5, 25e-6, 5.5), (r"TLV7\d\d", None, 0.2, 0.2, 35e-6, 5.5),
    (r"LP5907|LP2985|LP2992", None, 0.12, 0.25, 12e-6, 5.5), (r"MIC5504|MIC5219", None, 0.3, 0.3, 40e-6, 6),
    (r"SPX3819|MIC5205", None, 0.34, 0.5, 90e-6, 16), (r"LM2937|LM2940", None, 0.5, 0.5, 2e-3, 26),
    (r"L?M?78[0-9]{2}|78M\d\d|L78", None, 2.0, 1.0, 5e-3, 35), (r"LM317", None, 1.5, 1.5, 3e-3, 40),
    (r"NCP1117|LDL1117", None, 1.0, 1.0, 1e-3, 18), (r"XC6204|XC6201", None, 0.2, 0.15, 20e-6, 10),
]
LDO_DEFAULT = (None, 0.3, 0.3, 100e-6, 6)

# (regex on mpn/value, typical mA, peak mA): ICs modelled as loads on their supply pins
IC_LOADS = [
    (r"ESP32-?S3", 90, 380), (r"ESP32-?C[36]", 60, 350), (r"ESP32", 80, 380), (r"ESP8266|ESP-12", 80, 320),
    (r"RP2040|RP2350", 25, 50), (r"STM32H7", 120, 250), (r"STM32F4", 40, 90), (r"STM32", 20, 50),
    (r"ATMEGA32U4", 15, 30), (r"ATMEGA", 8, 20), (r"ATTINY", 3, 10), (r"ATSAMD|SAMD2", 8, 20),
    (r"CH32V|CH32X", 5, 15), (r"CH340|CH341", 15, 25), (r"CP210", 20, 30), (r"FT232|FT231", 15, 25),
    (r"NRF52|NRF53", 5, 15), (r"W5500", 130, 180), (r"ENC28J60", 160, 180), (r"SX127|RFM9", 12, 120),
    (r"NRF24", 12, 15), (r"WS2812|SK6812|WS2813|APA102|SK9822", 20, 60), (r"MAX7219", 5, 330),
    (r"74(HC|HCT|LVC|AHC|AC)\w*|74\w*1G\w*", 0.1, 5), (r"BME280|BMP280|BME680|SHT[34]\d|AHT\d+|HDC10", 0.5, 2),
    (r"MPU-?6050|MPU-?9250|ICM-?\d+|LSM6|ADXL|LIS3|BMI\d+", 4, 6), (r"INA2\d\d", 0.5, 1), (r"AT24C|24LC|24AA", 1, 3),
    (r"W25Q|GD25Q|AT25|IS25", 5, 25), (r"SSD1306", 10, 25), (r"DS3231", 0.2, 0.6), (r"DS18B20", 1, 1.5),
    (r"TP4056", 1, 1), (r"CH224|IP2721|HUSB238", 1, 2), (r"MAX485|SP3485|SN65HVD", 1, 60),
    (r"TJA1050|MCP2551|SN65HVD23", 10, 70), (r"PCA9685|MCP23017|PCF8574", 6, 25),
]

TRANSISTOR_PARTS = {
    "nmos": [(r"AO3400|AO3416", 1.05, 30, 5.7), (r"2N7002", 2.1, 60, 0.3), (r"BSS138", 1.3, 50, 0.22),
             (r"SI2302|SI2300", 0.9, 20, 2.6), (r"IRLML2502|IRLML6344", 0.9, 20, 4.2), (r"AO3402|DMN\w+", 1.1, 30, 4)],
    "pmos": [(r"AO3401|AO3407|AO3415", -0.9, 30, 4), (r"SI2301|SI2305|SI2333", -0.8, 20, 2.3),
             (r"DMG2305|DMG2307", -0.9, 20, 4.2), (r"IRLML6402", -0.8, 20, 3.7), (r"BSS84", -1.6, 50, 0.13)],
}


def _theta(fp: str) -> float:
    fpu = fp.upper()
    for key, th in THETA_JA:
        if key in fpu:
            return th
    return 150


# ---------------------------------------------------------------------------------- parts
class Part:
    def __init__(self, comp: dict, pads: dict[str, str | None], names: dict[str, str],
                 types: dict[str, str], units: dict[str, int], info: dict):
        self.c = comp
        self.ref = comp["ref"]
        self.pads = pads              # pad -> net name (None when unconnected)
        self.names = names            # pad -> pin name (upper case, overbars removed)
        self.types = types            # pad -> pin electrical type
        self.units = units            # pad -> symbol unit
        self.info = info              # JLCPCB part info (description, package) or {}
        self.sim = comp.get("sim") or {}
        self.kind = ""
        self.sub = ""
        self.data: dict = {}          # model details for the checks

    @property
    def blob(self) -> str:
        c = self.c
        return " ".join(str(c.get(k) or "") for k in ("value", "mpn", "symbol", "footprint", "description")) \
            + " " + (self.info.get("description") or "")

    @property
    def part_no(self) -> str:
        return f"{self.c.get('mpn') or ''} {self.c.get('value') or ''} {self.info.get('mpn') or ''}".upper()

    def pad_named(self, *names: str) -> str | None:
        want = {n.upper() for n in names}
        for pad, nm in self.names.items():
            if nm in want and self.pads.get(pad):
                return pad
        return None

    def facts(self) -> dict:
        return facts(f"{self.c.get('description') or ''} {self.info.get('description') or ''}")


def classify(p: Part) -> str:
    c, sim = p.c, p.sim
    if sim.get("skip"):
        return "skip"
    if sim.get("spice"):
        return "spice"
    if sim.get("model"):
        return str(sim["model"])
    prefix = re.match(r"[A-Z]+", p.ref).group(0)
    sym = (c.get("symbol") or "").lower()
    fp = (c.get("footprint") or "").lower()
    blob = p.blob.lower()
    pn = p.part_no
    if fp.startswith("mountinghole") or prefix in ("H", "MH", "FID", "TP", "LOGO", "G", "NT") \
            or "testpoint" in fp or "fiducial" in fp or sym.startswith("mechanical:"):
        return "skip"
    if re.search(r"WS281|SK68|APA10|SK98", pn) or "addressable" in blob:
        return "ic"
    if prefix == "LED" or sym.startswith("device:led") or fp.startswith("led_") or (
            prefix == "D" and re.search(r"\bled\b", blob)):
        return "led"
    if prefix in ("RN", "RA") or "r_array" in fp or "r_pack" in sym:
        return "res_array"
    if prefix == "R" or sym.startswith("device:r") or fp.startswith("resistor_"):
        return "resistor"
    if prefix == "C" or sym.startswith("device:c") or fp.startswith("capacitor_"):
        return "capacitor"
    if prefix == "FB" or "ferrite" in blob:
        return "ferrite"
    if prefix == "L" or sym.startswith("device:l") or fp.startswith("inductor_"):
        return "inductor"
    if prefix == "F" or "fuse" in blob or "polyfuse" in blob or "ptc" in blob:
        return "fuse"
    if prefix in ("JP", "SJ") or "jumper" in sym or "net_tie" in sym or "nettie" in fp:
        return "jumper"
    if prefix == "D" or sym.startswith("diode:") or sym.startswith("device:d"):
        if len([x for x in p.pads.values() if x]) > 2 or re.search(r"USBLC|TPD\dE|SRV05|PRTR5V", pn):
            return "skip"
        if re.search(r"tvs|esd|pesd|smbj|smaj|smf\d|transient", blob):
            return "tvs"
        if re.search(r"zener|bzt|mmsz|zmm|bzx|d_zener", blob):
            return "zener"
        return "diode"
    if prefix == "Q" or sym.startswith("transistor_") or "q_nmos" in sym or "q_pmos" in sym or "q_npn" in sym:
        return "mosfet" if re.search(r"mos|fet|q_[np]mos", sym + " " + blob) else "bjt"
    if prefix in ("SW", "S", "BTN", "KEY") or sym.startswith("switch:"):
        return "switch"
    if prefix in ("Y", "XTAL", "X") or "crystal" in blob or "resonator" in blob:
        return "skip"
    if prefix in ("BT", "BAT") or "battery" in sym:
        return "connector"
    if prefix in ("J", "P", "CN", "CON", "USB", "XT") or sym.startswith("connector") or fp.startswith("connector") \
            or fp.startswith("terminalblock"):
        return "connector"
    if prefix in ("K", "BZ", "LS", "M", "MK", "T", "TR", "ANT", "AE", "PS"):
        return "unsupported"
    if re.search(r"(NE|LM|NA|SA|TLC|LMC|ICM7?|TS)555|\b555\b", pn) or sym.startswith("timer:"):
        return "timer555" if "556" not in pn else "unsupported"
    if sym.startswith("comparator:") or re.search(r"LM393|LM339|LM2903|LM2901|LM311|TLV3\d0\d|MCP656\d|LMV331|LMV393", pn):
        return "comparator"
    if sym.startswith("amplifier_operational:") or re.search(
            r"LM358|LM324|LM321|LMV3\d\d|MCP60\d\d|MCP61\d\d|TLV9\d\d\d|TLV2\d\d|OPA\d|NE5532|TL07\d|TL08\d|AD86\d\d|SGM8\d\d", pn):
        return "opamp"
    if sym.startswith("regulator_switching:") or re.search(
            r"dc-dc|buck|boost|step-down|step-up|switching regulator|converter", blob):
        return "converter"
    if sym.startswith("regulator_linear:") or re.search(r"\bldo\b|linear regulator|low drop|voltage regulators - linear", blob) \
            or any(re.search(f[0], pn) for f in LDO_FAMILIES[:-3]):
        return "ldo"
    return "ic"


# ---------------------------------------------------------------------------------- netlist
class Netlist:
    def __init__(self, spec: dict, part_info=None, symbol_pins=None):
        self.spec = design.normalise(spec)
        self.sim = dict(self.spec.get("simulation") or {})
        self.part_info = part_info or (lambda code: None)
        self.symbol_pins = symbol_pins or _symbol_pins
        self.lines: list[str] = []
        self.models: dict[str, str] = {}
        self.subckts: set[str] = set()
        self.assumptions: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.parts: list[Part] = []
        self.sources: list[dict] = []
        self.loads: list[dict] = []
        self.ramps: list[str] = []    # supplies ramp up from 0 V in the transient, to show start-up
        self.nodes: dict[str, str] = {}
        self.ground = self._ground_net()
        self.pin_net = design.pin_net_map(self.spec)
        self.net_pins: dict[str, list[str]] = {}
        for pin, net in self.pin_net.items():
            self.net_pins.setdefault(net, []).append(pin)

    # -------------------------------------------------- nodes
    def _ground_net(self) -> str:
        names = [n["name"] for n in self.spec["nets"]]
        gp = self.spec["board"].get("ground_pour")
        if gp in names:
            return gp
        return next((n for n in names if n.upper() == "GND"), next((n for n in names if GND_RE.match(n)), "GND"))

    def node(self, net: str | None, ref: str = "", pad: str = "") -> str:
        if net is None:
            return f"nc_{ref}_{pad}".lower().replace("-", "_")
        if net == self.ground:
            return "0"
        if net not in self.nodes:
            base = re.sub(r"[^a-z0-9_]", "_", net.lower()).strip("_") or "n"
            self.nodes[net] = f"n{len(self.nodes) + 1}_{base}"[:40]
        return self.nodes[net]

    def net_of_node(self, node: str) -> str:
        if node == "0":
            return self.ground
        for k, v in self.nodes.items():
            if v == node:
                return k
        return node

    def is_power(self, net: str | None) -> bool:
        if not net or net == self.ground:
            return False
        cls = next((n.get("class") for n in self.spec["nets"] if n["name"] == net), "")
        return cls == "power" or net_volts(net) is not None or bool(re.match(r"^\+|^V(CC|DD|IN|BAT|BUS|SYS)", net.upper()))

    # -------------------------------------------------- build
    def build(self) -> str:
        for comp in self.spec["components"]:
            part = self._part(comp)
            part.kind = classify(part)
            self.parts.append(part)
        self.driven: dict[str, str] = {}   # net -> ref of the regulator that drives it
        for p in self.parts:
            try:
                getattr(self, f"_m_{p.kind}", self._m_unknown)(p)
            except Exception as e:  # noqa: BLE001 - one odd part must not stop the simulation
                self.warnings.append(f"{p.ref}: could not model it ({e}); left out of the simulation")
        self._auto_drive()
        self._sources()
        return self._text()

    def _part(self, comp: dict) -> Part:
        ref = comp["ref"]
        pads = {pin.split(".", 1)[1]: net for pin, net in self.pin_net.items() if pin.split(".", 1)[0] == ref}
        for nc in comp.get("no_connect") or []:
            pads.setdefault(str(nc).split(".")[-1], None)
        names, types, units = {}, {}, {}
        for pin in self.symbol_pins(comp.get("symbol")):
            names[pin["number"]] = _clean_name(pin["name"])
            types[pin["number"]] = pin["type"]
            units[pin["number"]] = pin.get("unit", 1)
        info = {}
        if comp.get("lcsc"):
            try:
                info = self.part_info(str(comp["lcsc"])) or {}
            except Exception:  # noqa: BLE001
                info = {}
        return Part(comp, pads, names, types, units, info)

    def add(self, line: str) -> None:
        self.lines.append(line)

    def _two(self, p: Part) -> tuple[str, str] | None:
        connected = [pad for pad, net in p.pads.items() if net]
        pads = sorted(p.pads, key=lambda s: (len(s), s))
        if len(connected) < 2 or len(pads) < 2:
            return None
        a, b = (pads[0], pads[1]) if {pads[0], pads[1]} <= set(p.pads) else (connected[0], connected[1])
        return self.node(p.pads.get(a), p.ref, a), self.node(p.pads.get(b), p.ref, b)

    # -------------------------------------------------- passive models
    def _m_resistor(self, p: Part) -> None:
        nodes = self._two(p)
        val = parse_value(p.c.get("value"))
        if not nodes:
            return
        if val is None:
            self.warnings.append(f"{p.ref}: value {p.c.get('value')!r} is not a resistance; left out")
            return
        f = p.facts()
        size = re.search(r"(0201|0402|0603|0805|1206|1210|2010|2512)", p.c.get("footprint") or "")
        rating = (f["watts"][0] if f["watts"] else RES_RATING.get(size[1]) if size else 0.25)
        p.data = {"r": max(val, 1e-3), "rating_w": rating, "nodes": nodes,
                  "rating_v": max(f["volts"]) if f["volts"] else None}
        self.add(f"R_{p.ref} {nodes[0]} {nodes[1]} {_fmt(max(val, 1e-3))}")

    def _m_res_array(self, p: Part) -> None:
        val = parse_value(p.c.get("value"))
        pads = sorted(p.pads, key=lambda s: (len(s), s))
        n = len(pads)
        if val is None or n < 2:
            return
        for i in range(n // 2):
            a, b = pads[i], pads[n - 1 - i]
            if p.pads.get(a) and p.pads.get(b):
                self.add(f"R_{p.ref}_{i + 1} {self.node(p.pads[a])} {self.node(p.pads[b])} {_fmt(val)}")
        self.assumptions.append(f"{p.ref}: resistor array with pads {pads[0]}-{pads[-1]}, {pads[1]}-{pads[-2]}... paired")

    def _m_capacitor(self, p: Part) -> None:
        nodes = self._two(p)
        val = parse_value(p.c.get("value"))
        if not nodes or val is None:
            return
        f = p.facts()
        polar = bool(re.search(r"cp_|c_polarized|polarized|electrolytic|tantalum", p.blob.lower()))
        p.data = {"c": val, "nodes": nodes, "rating_v": max(f["volts"]) if f["volts"] else None,
                  "polar": polar, "ceramic": bool(re.search(r"x5r|x7r|y5v|mlcc", p.blob.lower()))}
        self.add(f"C_{p.ref} {nodes[0]} {nodes[1]} {_fmt(val)}")

    def _m_inductor(self, p: Part) -> None:
        nodes = self._two(p)
        val = parse_value(p.c.get("value")) or 10e-6
        if not nodes:
            return
        self.add(f"L_{p.ref} {nodes[0]} l_{p.ref.lower()} {_fmt(val)}")
        self.add(f"R_{p.ref}_dcr l_{p.ref.lower()} {nodes[1]} 0.1")
        f = p.facts()
        p.data = {"rating_a": max(f["amps"]) if f["amps"] else None}

    def _m_ferrite(self, p: Part) -> None:
        nodes = self._two(p)
        if nodes:
            self.add(f"R_{p.ref} {nodes[0]} {nodes[1]} 0.1")
            f = p.facts()
            p.data = {"rating_a": max(f["amps"]) if f["amps"] else None}

    def _m_fuse(self, p: Part) -> None:
        nodes = self._two(p)
        if nodes:
            self.add(f"R_{p.ref} {nodes[0]} {nodes[1]} 0.05")
            f = p.facts()
            p.data = {"rating_a": min(f["amps"]) if f["amps"] else None}

    def _m_jumper(self, p: Part) -> None:
        nodes = self._two(p)
        if nodes:
            self.add(f"R_{p.ref} {nodes[0]} {nodes[1]} 0.001")

    # -------------------------------------------------- diodes
    def _ka(self, p: Part) -> tuple[str, str] | None:
        k = p.pad_named("K", "C", "CATHODE", "-") or ("1" if p.pads.get("1") else None)
        a = p.pad_named("A", "ANODE", "+") or ("2" if p.pads.get("2") else None)
        if not k or not a or k == a:
            return None
        return self.node(p.pads[k]), self.node(p.pads[a])

    def _m_led(self, p: Part) -> None:
        ka = self._ka(p)
        if not ka:
            return
        f = p.facts()
        blob = p.blob.lower()
        vf = p.sim.get("vf")
        how = "from sim.vf"
        if vf is None and f.get("v_range"):
            lo, hi = f["v_range"]
            vf, how = lo + 0.4 * (hi - lo), f"from the datasheet range {lo:g}-{hi:g} V"
        if vf is None:
            nm = f.get("nm")
            colours = [("infrared", 1.3), ("ir ", 1.3), ("red", 2.0), ("orange", 2.0), ("amber", 2.0),
                       ("yellow", 2.05), ("blue", 3.0), ("white", 3.0), ("warm", 3.0), ("uv", 3.3),
                       ("violet", 3.3), ("pink", 3.0), ("purple", 3.0), ("green", 2.9)]
            if nm:
                vf, how = (3.0 if nm < 540 else 2.1 if nm < 590 else 2.0), f"guessed from {nm:g} nm"
            else:
                for word, v in colours:
                    if word in blob:
                        vf, how = v, f"guessed for a {word.strip()} LED"
                        break
        if vf is None:
            vf, how = 2.0, "guessed (colour unknown)"
        itest = 0.01
        amps = [a for a in f["amps"] if a >= 1e-3]
        imax = p.sim.get("max_ma", None)
        imax = imax / 1000 if imax else (max(amps) if amps and max(amps) >= 0.015 else 0.02)
        rs = 4.0
        is_ = itest / math.exp((vf - itest * rs) / (2 * VT))
        model = f"DLED_{p.ref}"
        self.models[model] = f".model {model} D(IS={is_:.4g} N=2 RS={rs} BV=5 IBV=10u)"
        self.add(f"D_{p.ref} {ka[1]} {ka[0]} {model}")
        p.data = {"vf": vf, "imax": imax, "pmax": min(f["watts"]) if f["watts"] else None, "nodes": ka}
        if "sim.vf" not in how and "datasheet" not in how:
            self.assumptions.append(f"{p.ref}: LED forward voltage {vf:g} V {how}")

    def _diode_model(self, p: Part, kind: str) -> str:
        f = p.facts()
        pn = p.part_no
        blob = p.blob.lower()
        if kind == "zener":
            bv = p.sim.get("bv") or _zener_volts(pn + " " + str(p.c.get("value")))
            if not bv:
                bv = 5.1
                self.assumptions.append(f"{p.ref}: zener voltage unknown, assumed 5.1 V (set sim.bv)")
            return f"D(IS=1e-12 N=1.5 RS=2 BV={bv:g} IBV=5m)"
        schottky = bool(re.search(r"schottky|d_schottky|\bss\d\d|b5819|bat54|bat43|1n58\d\d|mbr|sk\d\d|rb\d{3}|pmeg", blob + " " + pn.lower()))
        n, rs = (1.1, 0.03) if schottky else (1.8, 0.04)
        if f.get("v_at"):
            vf, i = f["v_at"]
        elif schottky:
            vf, i = 0.45, 1.0
        elif re.search(r"4148|4448|bav\d|bas\d", pn.lower()):
            vf, i, n, rs = 0.72, 0.01, 1.75, 0.6
        else:
            vf, i = 1.0, 1.0
        is_ = i / math.exp(max(vf - i * rs, 0.05) / (n * VT))
        bv = max([v for v in f["volts"] if v >= 5] or [40 if schottky else 100])
        p.data.update({"vr": bv, "rating_a": max(f["amps"]) if f["amps"] else None, "schottky": schottky})
        return f"D(IS={is_:.4g} N={n} RS={rs} BV={bv:g} IBV=1u)"

    def _m_diode(self, p: Part, kind: str = "diode") -> None:
        ka = self._ka(p)
        if not ka:
            return
        model = f"D_{p.ref}M"
        self.models[model] = f".model {model} {self._diode_model(p, kind)}"
        self.add(f"D_{p.ref} {ka[1]} {ka[0]} {model}")
        p.data["nodes"] = ka

    def _m_zener(self, p: Part) -> None:
        self._m_diode(p, "zener")

    def _m_tvs(self, p: Part) -> None:
        pads = [pad for pad, net in p.pads.items() if net]
        if len(pads) < 2:
            return
        sv = p.sim.get("bv") or _zener_volts(p.part_no) or next(iter(sorted(v for v in p.facts()["volts"] if v >= 3)), None)
        if not sv:
            sv = 5.0
            self.assumptions.append(f"{p.ref}: TVS stand-off voltage unknown, assumed 5 V")
        bv = sv * 1.15
        model = f"DTVS_{p.ref}"
        self.models[model] = f".model {model} D(IS=1e-12 N=1.5 RS=0.5 BV={bv:.3g} IBV=1m)"
        bidir = "A1" in p.names.values() or "bidirectional" in p.blob.lower() or re.search(r"PESD\w*B|CA\b", p.part_no)
        if bidir:
            a, b = self.node(p.pads[pads[0]]), self.node(p.pads[pads[1]])
            mid = f"tvs_{p.ref.lower()}"
            self.add(f"D_{p.ref}a {mid} {a} {model}")
            self.add(f"D_{p.ref}b {mid} {b} {model}")
        else:
            ka = self._ka(p)
            if ka:
                self.add(f"D_{p.ref} {ka[1]} {ka[0]} {model}")
        p.data = {"standoff": sv}

    # -------------------------------------------------- transistors
    def _pins_from_suffix(self, p: Part, default: str) -> dict:
        """Pad -> pin letter from names, or a symbol suffix such as _GSD / _BEC."""
        letters = {}
        for pad, nm in p.names.items():
            if nm in ("G", "D", "S", "B", "C", "E"):
                letters[nm] = pad
        if len(letters) >= 3:
            return letters
        m = re.search(r"_([GDSBCE]{3})\b", (p.c.get("symbol") or "") + " " + (p.c.get("footprint") or ""))
        order = m[1] if m else default
        return {ch: str(i + 1) for i, ch in enumerate(order)}

    def _m_mosfet(self, p: Part) -> None:
        pn, blob = p.part_no, p.blob.lower()
        pch = bool(re.search(r"pmos|p-channel|p-ch|q_pmos|pchannel", blob)) or any(
            re.search(rx, pn) for rx, *_ in TRANSISTOR_PARTS["pmos"])
        pins = self._pins_from_suffix(p, "GSD")
        if not all(k in pins for k in "GDS"):
            return
        d, g, s = (self.node(p.pads.get(pins[k]), p.ref, pins[k]) for k in "DGS")
        vth, vds, ida = (-1.0 if pch else 1.5), 30, 1.0
        for rx, vt, vd, ia in TRANSISTOR_PARTS["pmos" if pch else "nmos"]:
            if re.search(rx, pn):
                vth, vds, ida = vt, vd, ia
                break
        else:
            self.assumptions.append(f"{p.ref}: generic {'P' if pch else 'N'}-MOSFET, Vth {vth:g} V")
        if p.sim.get("vth") is not None:
            vth = float(p.sim["vth"])
        f = p.facts()
        vds = max([v for v in f["volts"] if v >= 8] or [vds])
        ida = max([a for a in f["amps"] if a >= 0.05] or [ida])
        model = f"M_{p.ref}M"
        self.models[model] = f".model {model} {'PMOS' if pch else 'NMOS'}(VTO={vth:g} KP={4 if pch else 8} LAMBDA=0.01)"
        self.models["DBODY"] = ".model DBODY D(IS=1e-11 N=1.3 RS=0.05)"
        self.add(f"M_{p.ref} {d} {g} {s} {s} {model} W=100u L=100u")
        self.add(f"D_{p.ref}body {d if pch else s} {s if pch else d} DBODY")
        p.data = {"vds": vds, "ida": ida, "pch": pch, "nodes": (d, g, s), "theta": _theta(p.c.get("footprint") or "")}

    def _m_bjt(self, p: Part) -> None:
        blob, pn = p.blob.lower(), p.part_no
        pnp = bool(re.search(r"pnp|q_pnp|3906|8550|bc85\d|bc55\d|s9012|mmbt4403|2n2907", blob + pn.lower()))
        pins = self._pins_from_suffix(p, "BEC")
        if not all(k in pins for k in "BCE"):
            return
        c, b, e = (self.node(p.pads.get(pins[k]), p.ref, pins[k]) for k in "CBE")
        model = "QPNP" if pnp else "QNPN"
        self.models[model] = (".model QPNP PNP(IS=1e-14 BF=200 VAF=100 RB=10 RC=1 CJE=10p CJC=5p TF=0.4n)" if pnp else
                              ".model QNPN NPN(IS=1e-14 BF=250 VAF=100 RB=10 RC=1 CJE=10p CJC=5p TF=0.4n)")
        self.add(f"Q_{p.ref} {c} {b} {e} {model}")
        f = p.facts()
        p.data = {"vce": max([v for v in f["volts"] if v >= 10] or [40]),
                  "ica": max([a for a in f["amps"] if a >= 0.05] or [0.2]), "nodes": (c, b, e),
                  "theta": _theta(p.c.get("footprint") or "")}

    # -------------------------------------------------- switches and connectors
    def _m_switch(self, p: Part) -> None:
        closed = p.sim.get("closed")
        names = {nm: pad for pad, nm in p.names.items()}
        connected = [pad for pad, net in p.pads.items() if net]
        blob = p.blob.lower()
        push = bool(re.search(r"push|tact|button|momentary|ts-\d|\bkey\b", blob)) and not re.search(
            r"slide|toggle|rocker|dip|sw_spdt|sw_dpdt|sw_spst\b", blob)
        if closed is None:
            if push:
                closed = []
                self.assumptions.append(f"{p.ref}: pushbutton simulated not pressed")
            elif "B" in names and ("A" in names or "C" in names):
                throw = names["A"] if p.pads.get(names.get("A")) else names.get("C")
                closed = [[names["B"], throw]] if throw else []
                self.assumptions.append(f"{p.ref}: switch simulated in the {p.names.get(throw)} position (B-{p.names.get(throw)} closed)")
            elif len(connected) == 2:
                closed = [connected]
                self.assumptions.append(f"{p.ref}: switch simulated closed")
            else:
                closed = []
                self.assumptions.append(f"{p.ref}: switch contacts simulated open (set sim.closed)")
        for i, pair in enumerate(closed):
            a, b = (str(x) for x in pair)
            if p.pads.get(a) and p.pads.get(b):
                self.add(f"R_{p.ref}_c{i} {self.node(p.pads[a])} {self.node(p.pads[b])} 0.05")
        f = p.facts()
        p.data = {"rating_a": min([a for a in f["amps"] if a >= 0.01] or [0]) or None, "contacts": len(closed)}

    def _m_connector(self, p: Part) -> None:
        pass   # supplies are added in _sources

    def _m_skip(self, p: Part) -> None:
        pass

    def _m_unsupported(self, p: Part) -> None:
        self.assumptions.append(f"{p.ref} ({p.c.get('value')}): no simulation model; left out "
                                f"(give sim.spice or sim.load_ma)")

    def _m_unknown(self, p: Part) -> None:
        self._m_unsupported(p)

    def _m_spice(self, p: Part) -> None:
        text = str(p.sim["spice"])
        repl = {"ref": p.ref, "gnd": "0"}

        def sub(m):
            key = m[1]
            if key in repl:
                return repl[key]
            if key in p.pads:
                return self.node(p.pads[key], p.ref, key)
            raise ValueError(f"sim.spice uses {{{key}}} but {p.ref} has no such pad")
        for line in text.split("\n"):
            line = re.sub(r"\{([^}]+)\}", sub, line).strip()
            if line.startswith(".model") or line.startswith(".subckt") or line.startswith(".ends"):
                self.models[f"user_{len(self.models)}"] = line
            elif line:
                self.add(line)

    # -------------------------------------------------- ICs
    def _ground_pads(self, p: Part) -> list[str]:
        return [pad for pad, net in p.pads.items() if net == self.ground or (
            net and GND_RE.match(p.names.get(pad, "")) and not self.is_power(net))]

    def _supply_pads(self, p: Part) -> list[str]:
        out = []
        for pad, net in p.pads.items():
            if not net or net == self.ground:
                continue
            nm = p.names.get(pad, "")
            if GND_RE.match(nm) or nm in ("V-", "VEE"):
                continue
            if p.types.get(pad) == "power_in" or re.match(r"^(VCC|VDD|V\+|VIN|VBAT|AVDD|DVDD|VDDA|VDDIO|IOVDD|VCCIO|VDD\d|VCC\d|3V3|5V|VS)\w*$", nm):
                out.append(pad)
            elif not p.names and self.is_power(net):
                out.append(pad)
        return out

    def _ic_range_check(self, p: Part, supply_nets: list[str]) -> None:
        rng = p.facts().get("v_range")
        if rng and 0.8 <= rng[0] < rng[1] <= 60:
            p.data["supply_range"] = rng
            p.data["supply_nets"] = supply_nets

    def _m_ic(self, p: Part) -> None:
        pn = p.part_no + " " + str(p.c.get("description") or "").upper()
        typ, peak, known = 2.0, 5.0, False
        for rx, t, pk in IC_LOADS:
            if re.search(rx, pn):
                typ, peak, known = t, pk, True
                break
        if p.sim.get("load_ma") is not None:
            typ, known = float(p.sim["load_ma"]), True
            peak = float(p.sim.get("peak_ma", max(typ, peak if peak > typ else typ)))
        elif p.sim.get("peak_ma") is not None:
            peak = float(p.sim["peak_ma"])
        supplies = sorted({p.pads[pad] for pad in self._supply_pads(p)})
        gnds = self._ground_pads(p)
        if not supplies:
            if p.kind == "ic" and len(p.pads) > 2:
                self.assumptions.append(f"{p.ref}: no supply pins found, so it draws nothing in the simulation")
            return
        gnode = self.node(p.pads[gnds[0]]) if gnds else "0"
        if not known:
            self.assumptions.append(f"{p.ref} ({p.c.get('value')}): modelled as a {typ:g} mA load "
                                    f"(peak {peak:g} mA); set sim.load_ma / peak_ma if it draws more")
        share_t, share_p = typ / 1000 / len(supplies), (peak - typ) / 1000 / len(supplies)
        for i, net in enumerate(supplies):
            n = self.node(net)
            self.add(f"B_{p.ref}_ld{i} {n} {gnode} I = ({share_t:.6g} + pk*{max(share_p, 0):.6g})"
                     f" * min(max(v({n},{gnode}), 0)/1.0, 1)")
        self.loads.append({"ref": p.ref, "nets": supplies, "typ_ma": typ, "peak_ma": peak})
        p.data.update({"typ_ma": typ, "peak_ma": peak, "gnode": gnode,
                       "supply": self.node(supplies[0])})
        self._ic_range_check(p, supplies)
        for pad, level in (p.sim.get("pins") or {}).items():
            self._drive(p, str(pad), level)

    def _drive(self, p: Part, pad: str, level) -> None:
        net = p.pads.get(pad)
        if pad not in p.pads:
            pad = next((k for k, nm in p.names.items() if nm == str(pad).upper()), pad)
            net = p.pads.get(pad)
        if not net:
            self.warnings.append(f"{p.ref}: sim.pins names pad {pad}, which is not connected")
            return
        n = self.node(net)
        src = p.data.get("supply")
        g = p.data.get("gnode", "0")
        if level == "high":
            expr = f"v({src},{g})" if src else "3.3"
        elif level == "low":
            expr = "0"
        else:
            expr = _fmt(float(level))
        self.add(f"B_{p.ref}_drv{pad} drv_{p.ref.lower()}_{pad.lower()} {g} V = {expr}")
        self.add(f"R_{p.ref}_drv{pad} drv_{p.ref.lower()}_{pad.lower()} {n} 25")
        p.data.setdefault("driven", []).append((pad, level))

    def _auto_drive(self) -> None:
        """MCU pins that light an LED through a resistor are driven so the LED is on."""
        leds = {p.ref: p for p in self.parts if p.kind == "led"}
        if not leds:
            return
        led_pins = {}
        for p in leds.values():
            k = p.pad_named("K") or "1"
            a = p.pad_named("A") or "2"
            led_pins[p.pads.get(a)] = "high"
            led_pins[p.pads.get(k)] = "low"
        res = [p for p in self.parts if p.kind == "resistor" and len([n for n in p.pads.values() if n]) == 2]
        for p in self.parts:
            if p.kind != "ic" or "supply" not in p.data:
                continue
            given = {str(k) for k in (p.sim.get("pins") or {})}
            for pad, net in p.pads.items():
                if not net or pad in given or self.is_power(net) or net == self.ground:
                    continue
                if p.types.get(pad) in ("power_in", "power_out", "input"):
                    continue
                level = led_pins.get(net)
                if level is None:
                    for r in res:
                        nets = list(r.pads.values())
                        if net in nets:
                            other = nets[1] if nets[0] == net else nets[0]
                            if other in led_pins:
                                level = led_pins[other]
                                break
                if level:
                    self._drive(p, pad, level)
                    self.assumptions.append(f"{p.ref} pin {pad} ({p.names.get(pad, pad)}) driven {level} "
                                            f"to light its LED")

    def _m_timer555(self, p: Part) -> None:
        want = {"GND": ("GND",), "TRIG": ("TRIG", "TR", "TRIGGER"), "OUT": ("OUT", "Q", "OUTPUT"),
                "RST": ("RST", "R", "RESET"), "CV": ("CV", "CONT", "CTRL", "CONTROL"),
                "THR": ("THR", "THRES", "THRESH", "THRESHOLD"), "DIS": ("DIS", "DISCH", "DISCHARGE"),
                "VCC": ("VCC", "V+", "VDD")}
        default = {"GND": "1", "TRIG": "2", "OUT": "3", "RST": "4", "CV": "5", "THR": "6", "DIS": "7", "VCC": "8"}
        nodes = []
        for key, aliases in want.items():
            pad = p.pad_named(*aliases) or (default[key] if not p.names else None)
            nodes.append(self.node(p.pads.get(pad) if pad else None, p.ref, key))
        cmos = bool(re.search(r"TLC555|LMC555|ICM7555|TS555|7555", p.part_no))
        sub = "WB555C" if cmos else "WB555"
        self.subckts.add(sub)
        self.add(f"X_{p.ref} {' '.join(nodes)} {sub}")
        p.data = {"cmos": cmos, "supply": nodes[7], "gnode": nodes[0]}
        self._ic_range_check(p, [p.pads.get(p.pad_named("VCC", "V+", "VDD") or "8")])
        if "supply_range" not in p.data:
            p.data["supply_range"] = (2.0, 15.0) if cmos else (4.5, 16.0)
            p.data["supply_nets"] = [p.pads.get(p.pad_named("VCC", "V+", "VDD") or "8")]

    def _m_opamp(self, p: Part, comparator: bool = False) -> None:
        vp_pad = p.pad_named("V+", "VCC", "VDD", "VS+", "+VS")
        vn_pad = p.pad_named("V-", "VEE", "VSS", "GND", "VS-", "-VS")
        vp = self.node(p.pads.get(vp_pad)) if vp_pad else None
        vn = self.node(p.pads.get(vn_pad)) if vn_pad else "0"
        if not vp:
            self.assumptions.append(f"{p.ref}: no supply pin found; op-amp left out")
            return
        rro = not re.search(r"LM358|LM324|LM321|LM2904|LM2902|NE5532|TL07|TL08|LM741", p.part_no)
        hr_hi, hr_lo = (0.02, 0.02) if rro else (1.5, 0.02)
        units: dict[int, dict] = {}
        for pad, nm in p.names.items():
            u = p.units.get(pad, 1)
            if nm == "+":
                units.setdefault(u, {})["p"] = pad
            elif nm == "-":
                units.setdefault(u, {})["n"] = pad
            elif p.types.get(pad) in ("output", "open_collector") or nm in ("", "~", "OUT"):
                if nm not in ("V+", "V-"):
                    units.setdefault(u, {})["o"] = pad
        if not units and not p.names:
            units = {1: {"o": "1", "n": "2", "p": "3"}, 2: {"p": "5", "n": "6", "o": "7"}}
        n_units = 0
        for u, pins in sorted(units.items()):
            if not all(k in pins for k in "pno") or not p.pads.get(pins["o"]):
                continue
            n_units += 1
            ip, inn, out = (self.node(p.pads.get(pins[k]), p.ref, pins[k]) for k in "pno")
            tag = f"{p.ref}_{u}"
            if comparator:
                oc = not re.search(r"TLV3\d0\d|MCP656\d|LMV7\d|TLV70\d", p.part_no)
                if oc:
                    self.add(f"B_{tag}_oc {out} {vn} I = {_u(f'v({inn},{ip})')}*v({out},{vn})/30")
                else:
                    self.add(f"B_{tag}_o o_{tag.lower()} {vn} V = {_u(f'v({ip},{inn})')}*v({vp},{vn})")
                    self.add(f"R_{tag}_o o_{tag.lower()} {out} 50")
            else:
                g, gf, o = f"g_{tag.lower()}", f"gf_{tag.lower()}", f"o_{tag.lower()}"
                self.add(f"B_{tag}_g {g} 0 V = max(min(1e5*v({ip},{inn}), v({vp})+0.5), v({vn})-0.5)")
                self.add(f"R_{tag}_p {g} {gf} 1k")
                self.add(f"C_{tag}_p {gf} 0 15.9u")
                self.add(f"B_{tag}_o {o} 0 V = max(min(v({gf}), v({vp})-{hr_hi}), v({vn})+{hr_lo})")
                self.add(f"R_{tag}_o {o} {out} 50")
        self.add(f"R_{p.ref}_iq {vp} {vn} {_fmt(max(1.0, 5.0 / (0.0005 * max(n_units, 1))))}")
        p.data = {"supply": vp, "gnode": vn}
        self._ic_range_check(p, [p.pads.get(vp_pad)])

    def _m_comparator(self, p: Part) -> None:
        self._m_opamp(p, comparator=True)

    def _vout_guess(self, p: Part, out_net: str | None) -> tuple[float | None, str]:
        if p.sim.get("vout") is not None:
            return float(p.sim["vout"]), "sim.vout"
        pn = p.part_no
        for rx in (r"[-_ ](\d{1,2}\.\d)\b", r"[-_ ](\d{1,2}\.\d)V", r"(\d)V(\d)", r"XC62\d\dP(\d)(\d)\d",
                   r"HT7[35](\d)(\d)", r"MCP170[0-3]-?(\d)(\d)", r"ME6211C(\d)(\d)", r"RT9013-(\d)(\d)",
                   r"TLV7\d\d(\d)(\d)", r"AP2112K?-(\d)\.(\d)", r"78(?:M)?(\d{2})\b", r"L?M?78L?(\d{2})"):
            m = re.search(rx, pn)
            if m:
                g = m.groups()
                v = float(g[0]) if len(g) == 1 and "." in g[0] else (float(f"{g[0]}.{g[1]}") if len(g) == 2 else float(g[0]))
                if rx.startswith("78") or rx.startswith("L?M?78"):
                    v = float(g[0])
                if 0.8 <= v <= 24:
                    return v, "part number"
        f = p.facts()
        if out_net and net_volts(out_net):
            return net_volts(out_net), f"the output net name {out_net}"
        return None, ""

    def _m_ldo(self, p: Part) -> None:
        vi = p.pad_named("VI", "VIN", "IN", "INPUT", "VCC")
        vo = p.pad_named("VO", "VOUT", "OUT", "OUTPUT")
        adj = p.pad_named("ADJ", "FB")
        en = p.pad_named("EN", "CE", "SHDN", "ON", "INH")
        gnd = p.pad_named("GND", "VSS")
        if not p.names:
            vi, gnd, vo = ("3", "1", "2") if "1117" in p.part_no else ("1", "2", "3")
        if not vi or not vo or not p.pads.get(vi) or not p.pads.get(vo):
            self.assumptions.append(f"{p.ref}: regulator pins not recognised; left out (give sim.spice)")
            return
        fam = next((f for f in LDO_FAMILIES if re.search(f[0], p.part_no)), None)
        _, _, drop, imax, iq, vin_max = fam or (None, *LDO_DEFAULT)
        f = p.facts()
        if f.get("v_at") and f["v_at"][0] < 3:
            drop = f["v_at"][0]
        bigv = [v for v in f["volts"] if v > 5]
        if bigv:
            vin_max = max(bigv)
        big_a = [a for a in f["amps"] if a >= 0.05]
        if big_a:
            imax = max(big_a)
        drop = float(p.sim.get("dropout", drop))
        imax = float(p.sim.get("max_ma", imax * 1000)) / 1000
        n_in, n_out = self.node(p.pads[vi]), self.node(p.pads[vo])
        n_g = self.node(p.pads.get(gnd)) if gnd and p.pads.get(gnd) else "0"
        t = p.ref.lower()
        if adj and p.pads.get(adj) and (p.sim.get("vref") is not None or not self._vout_guess(p, None)[0]):
            vref = float(p.sim.get("vref", 1.25))
            n_adj = self.node(p.pads[adj])
            target = f"(v({n_adj}) + {vref:g})"
            vout, how = None, f"adjustable, Vref {vref:g} V"
            self.add(f"R_{p.ref}_adj {n_adj} 0 1e9")
        else:
            vout, how = self._vout_guess(p, p.pads[vo])
            if vout is None:
                self.assumptions.append(f"{p.ref}: output voltage unknown; left out (set sim.vout)")
                return
            target = f"(v({n_g}) + {vout:g})"
        enable = f" * {_u(f'v({self.node(p.pads[en])},{n_g})-0.9')}" if en and p.pads.get(en) else ""
        self.add(f"B_{p.ref} reg_{t} 0 V = {_smin(target, _smax(f'v({n_in}) - {drop * 0.8:g}', f'v({n_g})'))}{enable}")
        self.add(f"V_{p.ref}_s reg_{t} regs_{t} 0")
        self.add(f"R_{p.ref}_o regs_{t} {n_out} 0.02")
        self.add(f"B_{p.ref}_in {n_in} {n_g} I = max(i(V_{p.ref}_s), 0) + {iq:g}*min(max(v({n_in},{n_g}),0), 1)")
        self.driven[p.pads[vo]] = p.ref
        p.data = {"vout": vout, "drop": drop, "imax": imax, "iq": iq, "vin_max": vin_max,
                  "in": n_in, "out": n_out, "g": n_g, "theta": _theta(p.c.get("footprint") or ""),
                  "sense": f"v_{p.ref.lower()}_s"}
        if how not in ("sim.vout", "part number"):
            self.assumptions.append(f"{p.ref}: output {vout if vout else ''} {'V ' if vout else ''}from {how}")
        if not fam and "dropout" not in p.sim:
            self.assumptions.append(f"{p.ref}: dropout assumed {drop:g} V, max {imax * 1000:g} mA")

    def _m_converter(self, p: Part) -> None:
        vi = p.pad_named("VIN", "VI", "IN", "VCC", "PVIN")
        fb = p.pad_named("FB", "VFB", "ADJ", "VOUT", "OUT")
        gnd = p.pad_named("GND", "PGND", "AGND")
        en = p.pad_named("EN", "CE", "SHDN", "RUN")
        if not vi or not p.pads.get(vi):
            self.assumptions.append(f"{p.ref}: switching regulator pins not recognised; left out")
            return
        out_net, vout, how = None, p.sim.get("vout"), "sim.vout"
        fb_net = p.pads.get(fb) if fb else None
        if fb_net:
            rs = [q for q in self.parts if q.kind == "resistor" and fb_net in q.pads.values()]
            rbot = [q for q in rs if self.ground in q.pads.values()]
            rtop = [q for q in rs if self.ground not in q.pads.values()]
            if rbot and rtop:
                out_net = next(n for n in rtop[0].pads.values() if n != fb_net)
                if vout is None:
                    vref = float(p.sim.get("vref", 0.8))
                    rt, rb = parse_value(rtop[0].c.get("value")), parse_value(rbot[0].c.get("value"))
                    if rt and rb:
                        vout = vref * (1 + rt / rb)
                        how = f"the {rtop[0].ref}/{rbot[0].ref} divider and Vref {vref:g} V"
                        if "vref" not in p.sim:
                            self.assumptions.append(f"{p.ref}: feedback reference assumed 0.8 V (set sim.vref)")
            else:
                out_net = fb_net
        if out_net is None or vout is None:
            v2, how2 = self._vout_guess(p, out_net)
            vout = vout if vout is not None else v2
            how = how if p.sim.get("vout") is not None else how2
        if out_net is None or vout is None:
            self.assumptions.append(f"{p.ref}: switching regulator output not found; left out (set sim.vout "
                                    f"and make FB connect to the output divider)")
            return
        n_in, n_out = self.node(p.pads[vi]), self.node(out_net)
        n_g = self.node(p.pads.get(gnd)) if gnd and p.pads.get(gnd) else "0"
        eff = float(p.sim.get("efficiency", 0.85))
        t = p.ref.lower()
        enable = f" * {_u(f'v({self.node(p.pads[en])},{n_g})-0.9')}" if en and p.pads.get(en) else ""
        self.add(f"B_{p.ref} reg_{t} 0 V = {_smin(f'{vout:g}', _smax(f'v({n_in},{n_g}) * 6', '0'))}{enable}")
        self.add(f"V_{p.ref}_s reg_{t} regs_{t} 0")
        self.add(f"R_{p.ref}_o regs_{t} {n_out} 0.02")
        self.add(f"B_{p.ref}_in {n_in} {n_g} I = max(i(V_{p.ref}_s), 0) * {vout:g} / ({eff:g} * max(v({n_in},{n_g}), 0.5)) + 0.0005")
        self.driven[out_net] = p.ref
        f = p.facts()
        imax = float(p.sim.get("max_ma", 1000 * max([a for a in f["amps"] if a >= 0.1] or [1.0]))) / 1000
        p.data = {"vout": vout, "imax": imax, "in": n_in, "out": n_out, "g": n_g, "sense": f"v_{t}_s",
                  "vin_range": f.get("v_range"), "converter": True}
        self.assumptions.append(f"{p.ref}: switching regulator simulated as an ideal {vout:.3g} V supply "
                                f"({eff:.0%} efficient) from {how}; ripple is not simulated")

    # -------------------------------------------------- supplies
    def _sources(self) -> None:
        given = list(self.sim.get("sources") or [])
        used = set()
        for s in given:
            net = s.get("net")
            if net not in self.net_pins and net != self.ground:
                self.errors.append(f"simulation source net {net!r} is not a net of the design")
                continue
            self._add_source(dict(s))
            used.add(net)
        if any((s.get("kind") or "") != "signal" for s in given):
            return
        # Guess supplies: power nets on connectors and batteries that no regulator drives.
        for p in self.parts:
            if p.kind != "connector":
                continue
            blob = p.blob.lower()
            for pad, net in p.pads.items():
                if not net or net == self.ground or net in used or net in self.driven:
                    continue
                if not self.is_power(net) and p.names.get(pad) not in ("VBUS", "VCC", "V+", "+"):
                    continue
                volts, kind, how = None, "bench", ""
                cap = None
                if "usb" in blob and (p.names.get(pad) == "VBUS" or "VBUS" in net.upper() or net_volts(net) == 5):
                    volts, kind, how = 5.0, "usb", "USB connector"
                elif p.ref.startswith("BT") or "battery" in blob or "cr2032" in blob:
                    bat = _battery(blob)
                    if bat:
                        volts, cap, kind, how = bat[0], bat[1], "battery", bat[2]
                        ohms, max_ma = bat[3], bat[4]
                if volts is None and re.search(r"(\d+\.?\d*)\s*v\b", blob):
                    m = re.search(r"(\d+\.?\d*)\s*v\s*(battery|supply|input|dc|in\b)?", blob)
                    if m and 1 <= float(m[1]) <= 48:
                        volts, how = float(m[1]), f"{p.ref}'s description"
                        if m[2] == "battery" or "battery" in blob:
                            bat = _battery(f"{m[1]}v battery")
                            if bat:
                                kind, cap, ohms, max_ma = "battery", bat[1], bat[3], bat[4]
                if volts is None and net_volts(net):
                    volts, how = net_volts(net), f"the net name {net}"
                if volts is None:
                    self.warnings.append(f"{p.ref}: {net} comes from this connector but its voltage is unknown; "
                                         f"add it to simulation.sources")
                    continue
                src = {"net": net, "volts": volts, "kind": kind}
                if kind == "battery":
                    src.update({"capacity_mah": cap, "ohms": ohms, "max_ma": max_ma})
                self._add_source(src)
                used.add(net)
                self.assumptions.append(f"Supply: {net} = {volts:g} V from {how} ({p.ref})"
                                        + (f", {cap:g} mAh" if cap else ""))
        if not [s for s in self.sources if s["kind"] != "signal"]:
            for n in self.spec["nets"]:
                net = n["name"]
                if net in self.driven or net == self.ground or not net_volts(net) or net in used:
                    continue
                self._add_source({"net": net, "volts": net_volts(net), "kind": "bench"})
                self.assumptions.append(f"Supply: {net} = {net_volts(net):g} V from its name (no connector found)")
                break

    def _add_source(self, s: dict) -> None:
        kind = s.get("kind") or ("signal" if s.get("wave") else "bench")
        name = f"V_SRC{len(self.sources) + 1}"
        node = self.node(s["net"])
        volts = float(s.get("volts") or 0)
        ohms = float(s.get("ohms", {"usb": 0.3, "battery": 1.0, "bench": 0.05, "signal": 50}.get(kind, 0.1)))
        wave = s.get("wave") or {}
        ac = f" AC {float(s['ac']):g}" if s.get("ac") else ""
        if wave.get("type") == "sine":
            off, amp, f = float(wave.get("offset", 0)), float(wave.get("amplitude", 1)), float(wave.get("freq_hz", 1000))
            spec = f"DC {off:g}{ac} SIN({off:g} {amp:g} {f:g})"
            s["freq_hz"] = f
        elif wave.get("type") == "pulse":
            lo, hi, f = float(wave.get("low", 0)), float(wave.get("high", 3.3)), float(wave.get("freq_hz", 1000))
            duty = min(max(float(wave.get("duty", 0.5)), 0.01), 0.99)
            per = 1 / f
            edge = min(per / 100, 1e-6)
            spec = f"DC {lo:g}{ac} PULSE({lo:g} {hi:g} {per / 10:g} {edge:g} {edge:g} {duty * per - edge:g} {per:g})"
            s["freq_hz"] = f
        else:
            spec = f"DC {volts:g}{ac}"
            self.ramps.append(f"alter @{name.lower()}[pwl] = [ 0 0 1m {volts:g} ]")
        s.update({"kind": kind, "name": name.lower(), "node": node, "ohms": ohms})
        self.add(f"{name} src{len(self.sources) + 1} 0 {spec}")
        self.add(f"R_SRC{len(self.sources) + 1} src{len(self.sources) + 1} {node} {_fmt(max(ohms, 1e-3))}")
        self.sources.append(s)

    # -------------------------------------------------- text
    def _text(self) -> str:
        out = [f"* {self.spec['name']}: generated by WholeBoard", ".param pk=0"]
        for sub in sorted(self.subckts):
            out.append(SUBCKTS[sub])
        out += sorted(set(self.models.values()))
        out += self.lines
        return "\n".join(out) + "\n"


def _zener_volts(text: str) -> float | None:
    t = text.upper()
    m = re.search(r"(?:C|B|Z|-)(\d{1,2})V(\d)", t) or re.search(r"\b(\d{1,2})V(\d)\b", t)
    if m:
        return float(f"{m[1]}.{m[2]}")
    m = re.search(r"(?:SMBJ|SMAJ|SMF|P6KE|SMCJ|PESD|ESD\w*?)(\d{1,3}(?:\.\d)?)", t)
    if m:
        v = float(m[1])
        return v if v < 100 else v / 10
    m = re.search(r"(\d{1,2}\.\d)\s*V", t)
    return float(m[1]) if m else None


def _battery(blob: str):
    """(volts, mAh, description, internal ohms, max continuous mA) for common batteries."""
    b = blob.lower()
    table = [(r"cr2032", 3.0, 220, "a CR2032 coin cell", 15, 3), (r"cr2025", 3.0, 160, "a CR2025 coin cell", 20, 3),
             (r"cr1220", 3.0, 35, "a CR1220 coin cell", 40, 1), (r"cr123", 3.0, 1500, "a CR123A cell", 0.2, 1500),
             (r"18650", 3.7, 2500, "an 18650 Li-ion cell", 0.05, 2000), (r"lipo|li-po|li-ion|lithium|1s ", 3.7, 1000,
              "a Li-ion/LiPo cell", 0.1, 1000), (r"9\s*v", 9.0, 500, "a 9 V battery", 1.5, 100),
             (r"(4|four)\s*x?\s*aa\b", 6.0, 2500, "4×AA", 0.6, 1000), (r"(3|three)\s*x?\s*aaa?\b", 4.5, 2000, "3×AA", 0.45, 1000),
             (r"(2|two)\s*x?\s*aaa\b", 3.0, 1000, "2×AAA", 0.4, 500), (r"(2|two)\s*x?\s*aa\b", 3.0, 2500, "2×AA", 0.3, 1000),
             (r"\baaa\b", 1.5, 1000, "an AAA cell", 0.2, 500), (r"\baa\b", 1.5, 2500, "an AA cell", 0.15, 1000)]
    for rx, v, cap, desc, ohms, max_ma in table:
        if re.search(rx, b):
            return v, cap, desc, ohms, max_ma
    return None


def _smax(a: str, b: str) -> str:
    return f"(({a})+({b})+sqrt((({a})-({b}))^2+1e-4))/2"


def _smin(a: str, b: str) -> str:
    return f"(({a})+({b})-sqrt((({a})-({b}))^2+1e-4))/2"


def _u(x: str, k: int = 20) -> str:
    """A smooth 0..1 step (width about 1/k volts): hard switches stop the solver converging."""
    return f"(0.5+0.5*tanh({k}*({x})))"


def _sub555(name: str, cmos: bool) -> str:
    """555 timer: divider, two comparators and a regenerative set/reset latch (q, delayed in sd)."""
    rdiv, iq, drop, ro, rdis = ("100k", "100u", "0", "20", "30") if cmos else ("5k", "3m", "1.7", "8", "15")
    st = f"{_u('v(lo,gnd)-v(trig,gnd)', 500)}"
    rs = f"max({_u('v(thr,gnd)-v(cv,gnd)', 500)}, {_u('0.7-v(rst,gnd)', 50)})"
    on, off = _u("v(sd,gnd)-0.5", 100), _u("0.5-v(sd,gnd)", 100)
    return f""".subckt {name} gnd trig out rst cv thr dis vcc
R1 vcc cv {rdiv}
R2 cv lo {rdiv}
R3 lo gnd {rdiv}
Bq vcc gnd I = {iq}*min(max(v(vcc,gnd),0)/4.5, 1)
Bl q gnd V = {_u(f'{st} - {rs}*(1-{st}) + 2*v(sd,gnd) - 1', 100)}
Rd q sd 1k
Cd sd gnd 1n
Bo oi gnd V = {on}*max(v(vcc,gnd) - {drop}, 0.1) + {off}*0.1
Vs oi oo 0
Ro oo out {ro}
Bsup vcc gnd I = max(i(vs), 0)
Bd dis gnd I = {off}*v(dis,gnd)/{rdis}
.ends {name}"""


SUBCKTS = {"WB555": _sub555("WB555", False), "WB555C": _sub555("WB555C", True)}


_sym_cache: dict[str, list] = {}


def _symbol_pins(lib_id: str | None) -> list[dict]:
    if not lib_id:
        return []
    if lib_id not in _sym_cache:
        try:
            from .kicad import schematic
            info = schematic.symbol_info(lib_id)
            _sym_cache[lib_id] = info["pins"] if info else []
        except Exception:  # noqa: BLE001
            _sym_cache[lib_id] = []
    return _sym_cache[lib_id]


# ---------------------------------------------------------------------------------- planning
def _r_between(nl: Netlist, a: str | None, b: str | None) -> float:
    tot = 0.0
    for p in nl.parts:
        if p.kind == "resistor" and p.data.get("r") and {a, b} == set(p.pads.values()):
            tot += p.data["r"]
    return tot


def _plan(nl: Netlist) -> dict:
    sim = nl.sim
    periods, startup = [], 0.0
    for p in nl.parts:
        if p.kind != "timer555":
            continue
        thr = p.pads.get(p.pad_named("THR", "THRES", "THRESH") or "6")
        dis = p.pads.get(p.pad_named("DIS", "DISCH") or "7")
        vcc = p.pads.get(p.pad_named("VCC", "V+", "VDD") or "8")
        c = sum(q.data.get("c", 0) for q in nl.parts if q.kind == "capacitor"
                and set(q.pads.values()) == {thr, nl.ground})
        ra, rb = _r_between(nl, vcc, dis), _r_between(nl, dis, thr)
        if c and (ra or rb):
            periods.append(0.693 * (ra + 2 * rb) * c)
            startup = max(startup, 1.1 * (ra + rb) * c)
        elif c:
            rs = sum(q.data.get("r", 0) for q in nl.parts if q.kind == "resistor" and thr in q.pads.values())
            if rs:
                periods.append(1.4 * rs * c)
    for s in nl.sources:
        if s.get("freq_hz"):
            periods.append(1 / s["freq_hz"])
    if sim.get("time_ms"):
        stop = float(sim["time_ms"]) / 1000
    elif periods:
        stop = 6 * max(periods) + startup
    else:
        stop = 0.02
    stop = min(max(stop, 1e-4), 30.0)
    step = stop / 4000
    if periods:
        step = min(step, min(periods) / 100)
    plan = {"stop": stop, "step": step, "periods": periods}
    if periods and nl.loads and any(l["peak_ma"] > l["typ_ma"] for l in nl.loads):
        plan["peak_tran"] = min(stop, 6 * max(periods) + startup)
    ac = sim.get("ac")
    if ac and any(s.get("ac") for s in nl.sources):
        plan["ac"] = (float(ac.get("from_hz", 10)), float(ac.get("to_hz", 1e6)))
    return plan


def _control(plan: dict, ramps: list[str]) -> str:
    lines = [".options savecurrents rshunt=1e9 method=gear reltol=0.002 itl1=500 itl4=60",
             ".control", "set filetype=ascii", "op", "write op.raw all",
             "alterparam pk=1", "reset", "op", "write op_peak.raw all", "alterparam pk=0", "reset", *ramps,
             f"tran {plan['step']:.4g} {plan['stop']:.4g} 0 {max(plan['step'], plan['stop'] / 1000):.4g}",
             "write tran.raw all"]
    if plan.get("peak_tran"):
        # Circuits with no steady state (oscillators) have no reliable operating point, so the
        # peak-load case is also simulated in time.
        stop = plan["peak_tran"]
        lines += ["alterparam pk=1", "reset", *ramps,
                  f"tran {stop / 2000:.4g} {stop:.4g} 0 {stop / 1000:.4g}", "write tran_peak.raw all"]
    if plan.get("ac"):
        lines += [f"ac dec 30 {plan['ac'][0]:g} {plan['ac'][1]:g}", "write ac.raw all"]
    lines += [".endc", ".end"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------- results
class Waves:
    def __init__(self, raw: dict | None):
        self.d = (raw or {}).get("data") or {}
        self.x = self.d.get("time") or self.d.get("frequency") or []

    def settled(self, k0: int) -> "Waves":
        """The average of every vector from sample k0 on, as a one-point result."""
        t = self.x
        out = Waves(None)
        out.d = {k: [_mean(t, v, k0)] for k, v in self.d.items() if v and isinstance(v[0], float)}
        out.x = [t[-1]] if t else []
        return out

    def v(self, node: str) -> list[float] | None:
        if node == "0":
            return [0.0] * max(len(self.x), 1)
        node = node.lower()
        return self.d.get(f"v({node})") or self.d.get(node)

    def i(self, name: str) -> list[float] | None:
        name = name.lower()
        for k in (f"i({name})", f"{name}#branch", f"i(@{name}[i])", f"@{name}[i]", f"i(@{name}[id])",
                  f"@{name}[id]", f"i(@{name}[ic])", f"@{name}[ic]"):
            if k in self.d:
                return self.d[k]
        return None


def _window(t: list[float], frac: float) -> int:
    if not t:
        return 0
    t0 = t[-1] * frac
    for k, x in enumerate(t):
        if x >= t0:
            return k
    return 0


def _mean(t: list[float], y: list[float], k0: int) -> float:
    if len(y) <= k0 + 1 or len(t) != len(y):
        return y[-1] if y else 0.0
    area = sum((y[k] + y[k + 1]) / 2 * (t[k + 1] - t[k]) for k in range(k0, len(y) - 1))
    span = t[-1] - t[k0]
    return area / span if span > 0 else y[-1]


def _edges(t: list[float], y: list[float], k0: int) -> dict | None:
    seg = y[k0:]
    if len(seg) < 10:
        return None
    hi, lo = max(seg), min(seg)
    swing = hi - lo
    if swing < 0.3 or swing < 0.05 * max(abs(hi), abs(lo)):
        return None
    mid, hyst = (hi + lo) / 2, swing * 0.1
    state = seg[0] > mid
    rises, falls = [], []
    for k in range(k0 + 1, len(y)):
        if not state and y[k] > mid + hyst:
            state = True
            rises.append(t[k])
        elif state and y[k] < mid - hyst:
            state = False
            falls.append(t[k])
    if len(rises) < 2:
        return None
    period = (rises[-1] - rises[0]) / (len(rises) - 1)
    above = sum(t[k + 1] - t[k] for k in range(k0, len(y) - 1) if (y[k] + y[k + 1]) / 2 > mid)
    span = t[-1] - t[k0]
    return {"frequency_hz": 1 / period if period > 0 else None, "period_s": period,
            "duty": above / span if span else None, "high_v": hi, "low_v": lo, "cycles": len(rises)}


def _downsample(t: list[float], ys: dict[str, list[float]], n: int = 1200) -> dict:
    if len(t) <= n:
        return {"t": t, "series": ys}
    buckets = n // 2
    idx = []
    per = len(t) / buckets
    for b in range(buckets):
        a, z = int(b * per), max(int((b + 1) * per), int(b * per) + 1)
        idx.append(a)
        ref = next(iter(ys.values()), None)
        if ref:
            seg = range(a, min(z, len(t)))
            k_hi = max(seg, key=lambda k: ref[k])
            k_lo = min(seg, key=lambda k: ref[k])
            idx += sorted({k_hi, k_lo} - {a})
    idx = sorted(set(idx + [len(t) - 1]))
    return {"t": [t[k] for k in idx], "series": {k: [v[i] for i in idx] for k, v in ys.items()}}


def simulate(spec: dict, out_dir: Path, cfg: dict | None = None, engine: dict | None = None,
             part_info=None, log=print, cancel=None) -> dict:
    """Build the netlist, run ngspice and check the results. Never raises for circuit problems."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.raw"):
        old.unlink()
    nl = Netlist(spec, part_info=part_info)
    text = nl.build()
    res: dict = {"ran": False, "ok": True, "problems": list(nl.errors), "warnings": list(nl.warnings),
                 "assumptions": nl.assumptions, "engine": ngspice.label(engine)}
    if not [s for s in nl.sources if s["kind"] != "signal"]:
        res["warnings"].append("No supply found, so nothing was simulated. Add simulation.sources "
                               "(e.g. {\"net\": \"VIN\", \"volts\": 5}).")
        res["skipped"] = "no supply"
        return res
    if engine is None:
        res["skipped"] = "ngspice not found"
        res["warnings"].append("ngspice is not installed, so the circuit was not simulated.")
        return res
    plan = _plan(nl)
    cir = out_dir / "circuit.cir"
    cir.write_text(text + _control(plan, nl.ramps))
    log(f"Simulating {plan['stop'] * 1000:.4g} ms of operation with {ngspice.label(engine)}")
    try:
        output = ngspice.run(cir, engine, timeout=float((cfg or {}).get("tools", {}).get("sim_time_s", 120)),
                             cancel=cancel)
    except ngspice.SpiceError as e:
        res["problems"].append(f"Simulation failed: {e}")
        res["ok"] = False
        return res
    (out_dir / "ngspice.log").write_text(output)
    raws = {k: (out_dir / f"{k}.raw") for k in ("op", "op_peak", "tran", "tran_peak", "ac")}
    if not raws["op"].exists():
        errs = ngspice.errors_in(output)
        res["problems"].append("ngspice could not solve the circuit: " + ("; ".join(errs[:5]) or output[-600:]))
        res["ok"] = False
        return res
    res["ran"] = True
    data = {k: Waves(ngspice.read_raw(p)) if p.exists() else None for k, p in raws.items()}
    # An operating point that only "converged" by a transient ramp is not a steady state (an
    # oscillator has none): use the transient's settled averages instead.
    before_peak = output.split('"op_peak.raw"')[0] if '"op_peak.raw"' in output else output
    first_op = before_peak.split('"op.raw"')[0]
    if "Transient op started" in first_op and data["tran"]:
        data["op"] = data["tran"].settled(_window(data["tran"].x, 0.4))
        res["notes"] = ["The circuit has no steady operating point (it oscillates), so averages over "
                        "the transient are used instead."]
        if data.get("tran_peak"):
            data["op_peak"] = data["tran_peak"].settled(_window(data["tran_peak"].x, 0.4))
        elif data["op_peak"]:
            data["op_peak"] = data["op"]
    if data["tran"] is not None and data["tran"].x and data["tran"].x[-1] < plan["stop"] * 0.98:
        res["warnings"].append(f"The transient simulation stopped early at {data['tran'].x[-1] * 1000:.3g} ms: "
                               + "; ".join(ngspice.errors_in(output)[:3]))
        data["tran"] = None
    elif data["tran"] is None:
        res["warnings"].append("The transient simulation did not finish: " + "; ".join(ngspice.errors_in(output)[:3]))
    _analyse(nl, plan, data, res)
    res["ok"] = not res["problems"]
    (out_dir / "results.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def _analyse(nl: Netlist, plan: dict, data: dict, res: dict) -> None:
    op, opk, tr, ac = data["op"], data["op_peak"] or data["op"], data["tran"], data["ac"]
    t = tr.x if tr else []
    k0 = _window(t, 0.4 if plan["periods"] else 0.5)
    amb = float(nl.sim.get("ambient_c", 25))
    P, W = res["problems"], res["warnings"]

    def V(node: str, src: Waves | None = None) -> float:
        w = (src or op).v(node)
        return w[-1] if w else 0.0

    def tw(node: str) -> list[float] | None:
        return tr.v(node) if tr else None

    def settled(node: str) -> float:
        w = tw(node)
        return _mean(t, w, k0) if w else V(node)

    def span(node: str) -> tuple[float, float]:
        w = tw(node)
        vals = (w or []) + [V(node), V(node, opk)]
        return min(vals), max(vals)

    def diff_wave(a: str, b: str) -> list[float]:
        wa, wb = tw(a), tw(b)
        if wa and wb:
            return [x - y for x, y in zip(wa, wb)]
        return [V(a) - V(b)]

    # ---- rails and signals
    rails, signals = [], []
    for net, node in nl.nodes.items():
        w = tw(node)
        if nl.is_power(net):
            lo, hi = (min(w[k0:]), max(w[k0:])) if w and len(w) > k0 else (V(node), V(node))
            rails.append({"net": net, "volts": round(settled(node), 4), "op": round(V(node), 4),
                          "peak_load": round(V(node, opk), 4), "ripple_v": round(hi - lo, 4)})
        if w:
            e = _edges(t, w, k0)
            if e:
                signals.append({"net": net, **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in e.items()}})
    res["rails"] = sorted(rails, key=lambda r: -r["volts"])
    res["signals"] = signals

    # ---- supplies
    supplies = []
    for s in nl.sources:
        name = s["name"]
        typ = -(op.i(name) or [0])[-1]
        peak_op = -(opk.i(name) or [0])[-1]
        wi = tr.i(name) if tr else None
        avg = -_mean(t, wi, k0) if wi else typ
        inrush = -min(wi) if wi else typ
        row = {"net": s["net"], "kind": s["kind"], "volts": s.get("volts"),
               "current_ma": round(max(avg, typ) * 1000, 3), "peak_ma": round(max(peak_op, avg, typ) * 1000, 3),
               "startup_peak_ma": round(inrush * 1000, 3)}
        if s["kind"] == "signal":
            supplies.append(row)
            continue
        max_ma = s.get("max_ma") or {"usb": 500}.get(s["kind"])
        if max_ma:
            row["max_ma"] = max_ma
            if row["current_ma"] > max_ma:
                P.append(f"{s['net']} supplies {row['current_ma']:.0f} mA but the {s['kind']} supply is good for "
                         f"{max_ma:g} mA")
            elif peak_op * 1000 > max_ma:
                W.append(f"{s['net']} peaks at {peak_op * 1000:.0f} mA, above the {max_ma:g} mA the "
                         f"{s['kind']} supply is good for")
        if s.get("capacity_mah") and row["current_ma"] > 0.001:
            row["battery_hours"] = round(s["capacity_mah"] / row["current_ma"], 1)
        row["power_mw"] = round((s.get("volts") or 0) * row["current_ma"], 1)
        supplies.append(row)
    res["supplies"] = supplies

    # ---- parts
    rows = []

    def add(p: Part, **kv):
        rows.append({"ref": p.ref, "kind": p.kind, "value": p.c.get("value"),
                     **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in kv.items()}})

    for p in nl.parts:
        d = p.data
        k = p.kind
        if k == "resistor" and d.get("nodes"):
            a, b = d["nodes"]
            dv = diff_wave(a, b)
            pw = [x * x / d["r"] for x in dv]
            p_avg = _mean(t, pw, k0) if len(pw) == len(t) and t else pw[-1]
            p_op = (V(a, opk) - V(b, opk)) ** 2 / d["r"]
            pwr = max(p_avg, p_op, (V(a) - V(b)) ** 2 / d["r"])
            add(p, current_ma=math.sqrt(pwr / d["r"]) * 1000, power_mw=pwr * 1000, rating_mw=d["rating_w"] * 1000)
            if pwr > d["rating_w"]:
                P.append(f"{p.ref} ({p.c.get('value')}) dissipates {pwr * 1000:.0f} mW but is rated "
                         f"{d['rating_w'] * 1000:g} mW: use a bigger package or a higher value")
            elif pwr > 0.6 * d["rating_w"]:
                W.append(f"{p.ref} dissipates {pwr * 1000:.0f} mW, {pwr / d['rating_w']:.0%} of its rating; "
                         f"a bigger package would run cooler")
        elif k == "capacitor" and d.get("nodes"):
            a, b = d["nodes"]
            dv = diff_wave(a, b) + [V(a, opk) - V(b, opk)]
            vmax = max(abs(x) for x in dv)
            add(p, volts=vmax, rating_v=d.get("rating_v"))
            if d.get("rating_v"):
                if vmax > d["rating_v"]:
                    P.append(f"{p.ref} sees {vmax:.1f} V but is rated {d['rating_v']:g} V")
                elif vmax > 0.8 * d["rating_v"]:
                    W.append(f"{p.ref} sees {vmax:.1f} V, close to its {d['rating_v']:g} V rating")
                elif d.get("ceramic") and d["c"] >= 1e-6 and vmax > 0.5 * d["rating_v"]:
                    W.append(f"{p.ref}: a {p.c.get('value')} ceramic at {vmax:.1f} V of {d['rating_v']:g} V "
                             f"loses much of its capacitance (DC bias); consider a higher voltage rating")
            if d.get("polar") and min(dv) < -0.5:
                P.append(f"{p.ref} is a polarised capacitor connected backwards ({min(dv):.1f} V across it); "
                         f"pad 1 is +")
        elif k == "led" and d.get("nodes"):
            kk, aa = d["nodes"]
            wi = tr.i(f"d_{p.ref}") if tr else None
            ops = [(op.i(f"d_{p.ref}") or [0])[-1], (opk.i(f"d_{p.ref}") or [0])[-1]]
            seg = (wi[k0:] if wi else []) + ops
            imax_now = max(seg)
            on = [x for x in seg if x > 1e-4]
            i_on = sum(on) / len(on) if on else 0.0
            rev = max(V(kk) - V(aa), V(kk, opk) - V(aa, opk))
            add(p, current_ma=imax_now * 1000, on_current_ma=i_on * 1000, max_ma=d["imax"] * 1000, vf=d["vf"])
            if imax_now > d["imax"]:
                P.append(f"{p.ref} LED carries {imax_now * 1000:.1f} mA, over its {d['imax'] * 1000:g} mA "
                         f"limit: raise its series resistor")
            elif imax_now > 0.85 * d["imax"]:
                W.append(f"{p.ref} LED runs at {imax_now * 1000:.1f} mA, close to its {d['imax'] * 1000:g} mA limit")
            if rev > 1.0 and imax_now < 1e-5:
                P.append(f"{p.ref} LED is reverse biased ({rev:.1f} V) and never lights: check its polarity "
                         f"(pad 1 is the cathode)")
            elif 1e-6 < imax_now < 3e-4:
                W.append(f"{p.ref} LED only gets {imax_now * 1000:.2f} mA, which is very dim")
            elif imax_now <= 1e-6 and not rev > 1.0:
                W.append(f"{p.ref} LED never lights in the simulation (if a pin drives it, set that IC's sim.pins)")
        elif k in ("diode", "zener", "tvs") and (d.get("nodes") or k == "tvs"):
            if k == "tvs":
                continue
            kk, aa = d["nodes"]
            wi = tr.i(f"d_{p.ref}") if tr else None
            i_avg = max(_mean(t, wi, k0) if wi else 0, (op.i(f"d_{p.ref}") or [0])[-1])
            vrev = max(V(kk) - V(aa), V(kk, opk) - V(aa, opk), max((x for x in diff_wave(kk, aa)), default=0))
            vf = V(aa) - V(kk)
            pw = abs(i_avg * (vf if k != "zener" or vf > 0 else vrev))
            if k == "zener":
                pw = abs(i_avg) * max(abs(vf), vrev)
            temp = amb + pw * _theta(p.c.get("footprint") or "")
            add(p, current_ma=i_avg * 1000, reverse_v=vrev, power_mw=pw * 1000, temp_c=temp)
            if d.get("rating_a") and i_avg > d["rating_a"]:
                P.append(f"{p.ref} carries {i_avg * 1000:.0f} mA but is rated {d['rating_a'] * 1000:g} mA")
            if k == "diode" and d.get("vr") and vrev > d["vr"]:
                P.append(f"{p.ref} sees {vrev:.1f} V reverse but is rated {d['vr']:g} V")
            if temp > 125:
                P.append(f"{p.ref} would reach about {temp:.0f} °C ({pw * 1000:.0f} mW)")
        elif k == "mosfet" and d.get("nodes"):
            dn, gn, sn = d["nodes"]
            idv = abs((op.i(f"m_{p.ref}") or [0])[-1])
            idp = abs((opk.i(f"m_{p.ref}") or [0])[-1])
            wi = tr.i(f"m_{p.ref}") if tr else None
            if wi:
                idv = max(idv, abs(_mean(t, wi, k0)))
            vds = max(abs(x) for x in diff_wave(dn, sn))
            vgs = V(gn) - V(sn)
            pw = idv * abs(V(dn) - V(sn))
            temp = amb + pw * d["theta"]
            add(p, current_ma=idv * 1000, vds_max=vds, vgs=vgs, power_mw=pw * 1000, temp_c=temp)
            if vds > d["vds"]:
                P.append(f"{p.ref} sees {vds:.1f} V drain-source but is rated {d['vds']:g} V")
            if max(idv, idp) > d["ida"]:
                P.append(f"{p.ref} carries {max(idv, idp) * 1000:.0f} mA, above its {d['ida'] * 1000:g} mA rating")
            if idv > 0.01 and abs(V(dn) - V(sn)) > 0.3:
                W.append(f"{p.ref} is only partly on: {abs(V(dn) - V(sn)):.2f} V across it at "
                         f"{idv * 1000:.0f} mA (gate {vgs:.2f} V); drive the gate harder or use a logic-level part")
            if temp > 125:
                P.append(f"{p.ref} would reach about {temp:.0f} °C ({pw * 1000:.0f} mW)")
        elif k == "bjt" and d.get("nodes"):
            cn, bn, en = d["nodes"]
            ic = abs((op.i(f"q_{p.ref}") or [0])[-1])
            vce = max(abs(x) for x in diff_wave(cn, en))
            pw = ic * abs(V(cn) - V(en))
            temp = amb + pw * d["theta"]
            add(p, current_ma=ic * 1000, vce_max=vce, power_mw=pw * 1000, temp_c=temp)
            if ic > d["ica"]:
                P.append(f"{p.ref} carries {ic * 1000:.0f} mA, above its {d['ica'] * 1000:g} mA rating")
            if vce > d["vce"]:
                P.append(f"{p.ref} sees {vce:.1f} V collector-emitter but is rated {d['vce']:g} V")
            if temp > 125:
                P.append(f"{p.ref} would reach about {temp:.0f} °C ({pw * 1000:.0f} mW)")
        elif k == "switch" and d.get("contacts"):
            i_max = 0.0
            for idx in range(d["contacts"]):
                for src in (op, opk):
                    w = src.i(f"r_{p.ref}_c{idx}")
                    if w:
                        i_max = max(i_max, abs(w[-1]))
            add(p, current_ma=i_max * 1000, rating_ma=(d.get("rating_a") or 0) * 1000 or None)
            if d.get("rating_a") and i_max > d["rating_a"]:
                P.append(f"{p.ref} switches {i_max * 1000:.0f} mA but its contacts are rated "
                         f"{d['rating_a'] * 1000:g} mA")
        elif k in ("fuse", "ferrite", "inductor") and d.get("rating_a"):
            name = f"l_{p.ref}" if k == "inductor" else f"r_{p.ref}"
            i_max = max(abs((src.i(name) or [0])[-1]) for src in (op, opk))
            add(p, current_ma=i_max * 1000, rating_ma=d["rating_a"] * 1000)
            if i_max > d["rating_a"]:
                P.append(f"{p.ref} carries {i_max * 1000:.0f} mA but is rated {d['rating_a'] * 1000:g} mA")
        elif k in ("ldo", "converter") and d.get("in"):
            vin, vout = settled(d["in"]), settled(d["out"])
            vin_lo = span(d["in"])[0] if not tr else min((tw(d["in"]) or [vin])[k0:])
            iout = (op.i(d["sense"]) or [0])[-1]
            ipeak = (opk.i(d["sense"]) or [0])[-1]
            if tr and tr.i(d["sense"]):
                iout = max(iout, _mean(t, tr.i(d["sense"]), k0))
            row = {"vin": vin, "vout": vout, "current_ma": iout * 1000, "peak_ma": ipeak * 1000,
                   "max_ma": d["imax"] * 1000}
            if k == "ldo":
                pw = max(vin - vout, 0) * iout + vin * d["iq"]
                temp = amb + pw * d["theta"]
                row.update(power_mw=pw * 1000, temp_c=temp, dropout_v=d["drop"])
                target = d["vout"]
                if target and vin_lo < target + d["drop"] * 0.9 and vin_lo > 0.5:
                    P.append(f"{p.ref} is in dropout: its input falls to {vin_lo:.2f} V but it needs "
                             f"{target + d['drop']:.2f} V ({target:g} V + {d['drop']:g} V dropout); the output is "
                             f"only {vout:.2f} V. Use a low-dropout regulator or a higher input voltage")
                if d.get("vin_max") and span(d["in"])[1] > d["vin_max"]:
                    P.append(f"{p.ref} input reaches {span(d['in'])[1]:.1f} V, above its {d['vin_max']:g} V maximum")
                if temp > 125:
                    P.append(f"{p.ref} would reach about {temp:.0f} °C dissipating {pw * 1000:.0f} mW "
                             f"({vin - vout:.1f} V × {iout * 1000:.0f} mA): use a switching regulator, a lower "
                             f"input voltage or a bigger package")
                elif temp > 95:
                    W.append(f"{p.ref} runs hot: about {temp:.0f} °C dissipating {pw * 1000:.0f} mW; give it "
                             f"copper area or use a switching regulator")
            elif d.get("vin_range"):
                lo, hi = d["vin_range"]
                if span(d["in"])[1] > hi:
                    P.append(f"{p.ref} input reaches {span(d['in'])[1]:.1f} V, above its {hi:g} V maximum")
                if vin_lo < lo and vin_lo > 0.5:
                    W.append(f"{p.ref} input falls to {vin_lo:.2f} V, below its {lo:g} V minimum")
            if iout > d["imax"]:
                P.append(f"{p.ref} must supply {iout * 1000:.0f} mA but is rated {d['imax'] * 1000:g} mA")
            elif ipeak > d["imax"]:
                P.append(f"{p.ref} must supply {ipeak * 1000:.0f} mA at peak load (e.g. radio transmit) but is "
                         f"rated {d['imax'] * 1000:g} mA: use a bigger regulator")
            add(p, **row)
        if d.get("supply_range"):
            lo, hi = d["supply_range"]
            for net in d.get("supply_nets") or []:
                if not net:
                    continue
                vs_lo, vs_hi = span(nl.node(net))
                v_set = settled(nl.node(net))
                if vs_hi > hi * 1.03:
                    P.append(f"{p.ref} ({p.c.get('value')}) gets {vs_hi:.2f} V on {net} but its supply range "
                             f"is {lo:g}-{hi:g} V")
                elif v_set < lo * 0.97 and v_set > 0.3:
                    P.append(f"{p.ref} ({p.c.get('value')}) gets only {v_set:.2f} V on {net}; it needs "
                             f"{lo:g}-{hi:g} V")
        if k in ("timer555", "opamp", "comparator") and d.get("supply"):
            add(p, supply_v=settled(d["supply"]) - settled(d.get("gnode", "0")))
        if k == "ic" and d.get("typ_ma") is not None:
            add(p, current_ma=d["typ_ma"], peak_ma=d["peak_ma"],
                supply_v=settled(d["supply"]) if d.get("supply") else None)
    res["parts"] = rows
    res["loads"] = [dict(x) for x in nl.loads]
    res["ambient_c"] = amb

    # ---- per-net current, for the trace-width check after routing
    net_i: dict[str, float] = {}
    for s in supplies:
        net_i[s["net"]] = max(net_i.get(s["net"], 0), s.get("peak_ma") or 0)
    for r in rows:
        if r["kind"] in ("ldo", "converter"):
            p = next(q for q in nl.parts if q.ref == r["ref"])
            for node, cur in ((p.data["in"], r["current_ma"]), (p.data["out"], max(r["current_ma"], r.get("peak_ma") or 0))):
                net = nl.net_of_node(node)
                net_i[net] = max(net_i.get(net, 0), cur)
    for p in nl.parts:
        r = next((x for x in rows if x["ref"] == p.ref), None)
        if r and r.get("current_ma"):
            for net in p.pads.values():
                if net and net != nl.ground:
                    net_i[net] = max(net_i.get(net, 0), float(r["current_ma"]))
    if supplies:
        net_i[nl.ground] = sum(s.get("peak_ma") or 0 for s in supplies if s["kind"] != "signal")
    res["net_current_ma"] = {k: round(v, 2) for k, v in net_i.items()}

    # ---- AC sweep
    if ac and ac.x:
        amp = next((float(s["ac"]) for s in nl.sources if s.get("ac")), 1.0)
        probes = [n for n in (nl.sim.get("probes") or []) if n in nl.nodes]
        if not probes:
            probes = [e["net"] for e in nl.sim.get("expect") or [] if e.get("net") in nl.nodes][:3]
        curves = {}
        for net in probes[:6]:
            w = ac.v(nl.node(net))
            if w:
                curves[net] = [20 * math.log10(max(math.hypot(*c) / amp, 1e-12)) for c in w]
        res["ac"] = {"f": ac.x if not isinstance(ac.x[0], tuple) else [c[0] for c in ac.x], "db": curves}

    # ---- expectations
    exps = []
    for e in nl.sim.get("expect") or []:
        exps.append(_expect(e, nl, res, data, t, k0, settled, tw))
    res["expectations"] = exps
    blocking = nl.sim.get("blocking", True)
    for e in exps:
        if not e["ok"]:
            (P if blocking else W).append(f"Expected {e['text']}, simulated {e['measured']}")

    # ---- plots
    if tr and t:
        probes = [n for n in (nl.sim.get("probes") or []) if n in nl.nodes]
        names = list(dict.fromkeys(probes + [s["net"] for s in signals][:4] + [r["net"] for r in res["rails"]][:4]))[:8]
        ys = {n: tr.v(nl.node(n)) for n in names if tr.v(nl.node(n))}
        cur = {}
        for s in nl.sources:
            w = tr.i(s["name"])
            if w:
                cur[f"I({s['net']}) mA"] = [-x * 1000 for x in w]
        both = {**{f"{k} V": v for k, v in ys.items()}, **cur}
        res["plot"] = _downsample(t, both)
    res["sim_time_s"] = plan["stop"]


def _expect(e: dict, nl: Netlist, res: dict, data: dict, t, k0, settled, tw) -> dict:
    m = str(e.get("measure") or "voltage").lower()
    net, ref = e.get("net"), e.get("ref")
    lo, hi = e.get("min"), e.get("max")
    unit = {"frequency": "Hz", "duty": "", "voltage": "V", "average": "V", "ripple": "V", "min": "V", "max": "V",
            "current": "mA", "gain_db": "dB", "cutoff_hz": "Hz", "power": "mW"}.get(m, "")
    rng = (f"{lo:g}" if lo is not None else "") + (" to " if lo is not None and hi is not None else "") + \
        (f"{hi:g}" if hi is not None else "")
    if lo is not None and hi is None:
        rng = f"at least {lo:g}"
    elif hi is not None and lo is None:
        rng = f"at most {hi:g}"
    text = f"{m} of {net or ref} {rng} {unit}".strip()
    val = None
    node = nl.node(net) if net in nl.nodes or net == nl.ground else None
    if net and node is None:
        return {"text": text, "measured": f"no net {net}", "ok": False}
    if m in ("frequency", "duty"):
        sig = next((s for s in res.get("signals", []) if s["net"] == net), None)
        val = (sig or {}).get("frequency_hz" if m == "frequency" else "duty") if sig else 0.0
    elif m in ("voltage", "average"):
        val = settled(node)
    elif m in ("ripple", "min", "max"):
        w = tw(node)
        seg = w[k0:] if w else [settled(node)]
        val = {"ripple": max(seg) - min(seg), "min": min(seg), "max": max(seg)}[m]
    elif m in ("current", "power"):
        row = next((r for r in res.get("parts", []) if r["ref"] == ref), None)
        val = (row or {}).get("current_ma" if m == "current" else "power_mw")
        if val is None:
            return {"text": text, "measured": f"no {m} for {ref}", "ok": False}
    elif m in ("gain_db", "cutoff_hz"):
        ac = res.get("ac") or {}
        curve = (ac.get("db") or {}).get(net)
        if not curve:
            return {"text": text, "measured": "no AC sweep (add simulation.ac and a source with \"ac\": 1)", "ok": False}
        f = ac["f"]
        if m == "gain_db":
            target = float(e.get("freq_hz", 1000))
            k = min(range(len(f)), key=lambda i: abs(math.log10(max(f[i], 1e-9)) - math.log10(target)))
            val = curve[k]
        else:
            ref_db = max(curve)
            k = next((i for i in range(curve.index(ref_db), len(curve)) if curve[i] < ref_db - 3), None)
            val = f[k] if k is not None else None
            if val is None:
                return {"text": text, "measured": "no -3 dB point in the sweep", "ok": False}
    else:
        return {"text": text, "measured": f"unknown measure {m!r}", "ok": False}
    ok = val is not None and (lo is None or val >= lo) and (hi is None or val <= hi)
    shown = f"{val:.4g} {unit}".strip() if isinstance(val, (int, float)) else str(val)
    return {"text": text, "measured": shown, "ok": ok, "value": val}
