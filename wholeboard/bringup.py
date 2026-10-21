"""The bring-up guide: a checklist for powering a new board for the first time, from the simulation."""

from __future__ import annotations

import re

from . import design

FLASH = {
    "ESP32": "Hold BOOT, tap RESET (or plug in USB while holding BOOT), release BOOT, then `pio run -t upload`, "
             "over the chip's own USB port, the board's USB-serial bridge, or a 3.3 V USB-serial adapter on the "
             "UART0 pins.",
    "RP2040": "Hold BOOTSEL while plugging in USB: an RPI-RP2 drive appears. Copy firmware.uf2 onto it, or run "
              "`pio run -t upload`. With a blank flash the drive appears without the button.",
    "RP2350": "Hold BOOTSEL while plugging in USB: an RP2350 drive appears. Copy firmware.uf2 onto it, or run "
              "`pio run -t upload`.",
    "STM32": "Connect an ST-Link to SWDIO, SWCLK, GND and 3V3, then `pio run -t upload`.",
    "ATmega328P": "Connect a USBasp to the ISP pins (MOSI, MISO, SCK, RESET, VCC, GND); set the fuses once with "
                  "`pio run -t fuses`, then `pio run -t upload`.",
    "ATmega32U4": "Plug in USB; if the bootloader is missing, burn it once with an ISP programmer. Then "
                  "`pio run -t upload`.",
    "ATtiny85": "Connect a USBasp to the ISP pins, then `pio run -t upload`.",
}
FINE_PITCH = re.compile(r"QFN|DFN|QFP|BGA|LGA|P0\.[45]mm|P0\.65mm|WSON|USB_C|ESP32|RF_Module", re.I)
POLAR = re.compile(r"^(D|LED)\d|^C\d.*(Tantalum|Elec|CP_)|^(BT|BAT)\d", re.I)


def _f(v, unit: str, digits: int = 3) -> str:
    if v is None:
        return "–"
    return f"{float(v):.{digits}g} {unit}"


def guide(spec: dict, sim_res: dict | None, budget: dict | None, fw: dict | None) -> dict:
    spec = design.normalise(spec)
    sim_res = sim_res or {}
    budget = budget or {}
    comps = spec["components"]
    tps = {c["test_point_for"]: c["ref"] for c in comps if c.get("test_point_for")}
    gnd = next((n["name"] for n in spec["nets"] if re.match(r"^(GND|VSS|0V)$", n["name"], re.I)), "GND")
    rails = [r for r in sim_res.get("rails") or [] if r["net"] != gnd]
    supplies = [s for s in sim_res.get("supplies") or [] if s["kind"] != "signal"]
    steps: list[dict] = []

    def where(net: str) -> str:
        if net in tps:
            return f"test point {tps[net]}"
        pins = next((n["pins"] for n in spec["nets"] if n["name"] == net), [])
        return f"pad {pins[0]}" if pins else "–"

    # 1. look before powering
    fine = [c["ref"] for c in comps if FINE_PITCH.search(c.get("footprint") or "")]
    polar = [c["ref"] for c in comps if POLAR.search(f"{c['ref']} {c.get('footprint', '')}")
             or re.search(r"Device:(LED|D|D_Schottky|D_Zener|CP)\b", c.get("symbol") or "")]
    items = ["Check the boards against the 3D render: every part present, none lifted or rotated."]
    if fine:
        items.append(f"Under a magnifier, check for solder bridges between the fine-pitch pins of {', '.join(fine[:10])}.")
    if polar:
        items.append(f"Check the orientation of polarised parts: {', '.join(sorted(set(polar))[:16])} "
                     "(LED pad 1 is the cathode, marked on the silkscreen).")
    items.append("Hand-solder any parts JLCPCB did not fit (the BOM's no-LCSC lines), then clean off the flux.")
    steps.append({"title": "Look before powering", "items": items})

    # 2. resistance checks
    rows = [{"net": r["net"], "where": where(r["net"]), "expect": "more than 100 Ω (rising as capacitors charge)"}
            for r in rails]
    for s in supplies:
        if s["net"] not in [r["net"] for r in rails]:
            rows.insert(0, {"net": s["net"], "where": where(s["net"]), "expect": "more than 100 Ω"})
    steps.append({"title": "Check for shorts (power off)",
                  "items": [f"Set a multimeter to resistance and put the black probe on {gnd}"
                            + (f" ({tps[gnd]})" if gnd in tps else "") + ". Measure each rail below; a few ohms "
                            "or less is a short: find it before powering."],
                  "table": {"head": ["Rail", "Probe at", "Expect"],
                            "rows": [[r["net"], r["where"], r["expect"]] for r in rows]}})

    # 3. first power
    items, table = [], []
    for s in supplies:
        typ, peak = s.get("current_ma") or 0, s.get("peak_ma") or 0
        limit = max(typ * 1.5, typ + 30, 50)
        if s["kind"] == "battery":
            how = f"Use a bench supply at {_f(s.get('volts'), 'V')} instead of the battery for the first power-up"
        elif s["kind"] == "usb":
            how = "Use a current-limited bench supply at 5 V on VBUS, or a USB power meter between the charger and the board"
        else:
            how = f"Use a bench supply at {_f(s.get('volts'), 'V')}"
        items.append(f"{how}, with the current limit at about {limit:.0f} mA. Turn it on and watch the current.")
        table.append([s["net"], _f(s.get("volts"), "V"), f"{typ:.3g} mA", f"{peak:.3g} mA", f"{limit:.0f} mA"])
    items.append("If the supply hits its current limit, turn off at once and look for a short or a part fitted backwards.")
    steps.append({"title": "First power-up, current limited", "items": items,
                  "table": {"head": ["Input", "Voltage", "Expected current", "Peak (radio, motors)", "Set limit"],
                            "rows": table}})

    # 4. rails
    if rails:
        steps.append({"title": "Measure the rails", "items": ["Measure each rail against GND; within ±5 % is fine."],
                      "table": {"head": ["Rail", "Probe at", "Expect", "Range"],
                                "rows": [[r["net"], where(r["net"]), _f(r["volts"], "V"),
                                          f"{r['volts'] * 0.95:.3g} – {r['volts'] * 1.05:.3g} V"] for r in rails]}})

    # 5. heat
    warm = [t for t in budget.get("thermal") or [] if t["temp_c"] >= 40]
    items = ["After a minute, feel around the board (carefully) or use a thermal camera. Nothing should be too hot to "
             "touch, except the parts listed here."]
    for t in warm[:6]:
        items.append(f"{t['ref']} ({t['value']}) runs at about {t['temp_c']:.0f} °C, dissipating {t['power_mw']:.0f} mW.")
    steps.append({"title": "Check for heat", "items": items})

    # 6. signals and LEDs
    sig = [s for s in sim_res.get("signals") or [] if s.get("frequency_hz")]
    leds = [p for p in sim_res.get("parts") or [] if p["kind"] == "led" and (p.get("current_ma") or 0) > 0.3]
    if sig or leds:
        items = [f"{p['ref']} lights ({p['current_ma']:.2g} mA)." for p in leds]
        st = {"title": "Signals and indicators", "items": items}
        if sig:
            st["table"] = {"head": ["Net", "Probe at", "Frequency", "Duty"],
                           "rows": [[s["net"], where(s["net"]), _f(s["frequency_hz"], "Hz"),
                                     f"{s['duty'] * 100:.0f} %" if s.get("duty") is not None else "–"] for s in sig[:8]]}
            st["items"].insert(0, "With an oscilloscope (or a meter with a frequency range), check the signals:")
        steps.append(st)

    # 7. firmware
    if fw:
        fam = fw.get("family", "")
        key = next((k for k in FLASH if fam.startswith(k)), None)
        items = [f"The starter firmware for {fw.get('mcu')} ({fam}) is in the firmware zip.",
                 FLASH.get(key, "Flash it with `pio run -t upload`."),
                 "Open the serial monitor (`pio device monitor`, 115200 baud): it prints the pins it knows, blinks the "
                 "LEDs, reports button presses and scans I²C."]
        if fw.get("pins"):
            outs = [r for r in fw["pins"] if r.get("role") in ("led", "output")]
            if outs:
                items.append("Expect these outputs to toggle: " + ", ".join(f"{r['macro']} ({r['net']})" for r in outs[:8]) + ".")
        steps.append({"title": "Program it", "items": items})

    # 8. interfaces
    bus = [n["name"] for n in spec["nets"] if re.search(r"SDA|SCL|TX|RX|CAN|MOSI|MISO|SCK|USB_D|D\+|D-|RS485",
                                                         n["name"], re.I)]
    if bus:
        steps.append({"title": "Interfaces", "items": [
            "Check each interface with real hardware: " + ", ".join(bus[:12]) + ".",
            "I²C: the starter firmware's scan should list every device's address. USB: the board should enumerate "
            "(lsusb / Device Manager). UART/CAN/RS-485: loop back or talk to a known-good device."]})

    md = [f"# Bring-up guide: {spec.get('name')}", "", spec.get("summary") or "", ""]
    for i, st in enumerate(steps, 1):
        md += [f"## {i}. {st['title']}", ""]
        md += [f"- [ ] {x}" for x in st["items"]]
        if st.get("table") and st["table"]["rows"]:
            md += ["", "| " + " | ".join(st["table"]["head"]) + " |", "|" + "---|" * len(st["table"]["head"])]
            md += ["| " + " | ".join(str(c) for c in r) + " |" for r in st["table"]["rows"]]
        md.append("")
    return {"steps": steps, "test_points": [{"ref": r, "net": n} for n, r in tps.items()], "markdown": "\n".join(md)}
