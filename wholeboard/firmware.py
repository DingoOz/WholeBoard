"""Starter firmware for boards with a microcontroller: a PlatformIO (Arduino) project whose pins.h
names every GPIO after the net it drives, plus a pin map for the UI.

Pin names come from the KiCad symbol (IO4, GPIO4, PA5, PD3 …), so the map is only as good as the
symbol; parts drawn with a generated box symbol get no map.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .kicad import schematic

# family: (pattern on symbol/value/mpn, PlatformIO env lines, notes)
FAMILIES = [
    ("esp32s3", r"ESP32-?S3", ["platform = espressif32", "board = esp32-s3-devkitc-1", "framework = arduino"], "ESP32-S3"),
    ("esp32s2", r"ESP32-?S2", ["platform = espressif32", "board = esp32-s2-saola-1", "framework = arduino"], "ESP32-S2"),
    ("esp32c3", r"ESP32-?C3|ESP8685", ["platform = espressif32", "board = esp32-c3-devkitm-1", "framework = arduino"], "ESP32-C3"),
    ("esp32c6", r"ESP32-?C6", ["platform = espressif32", "board = esp32-c6-devkitc-1", "framework = arduino"], "ESP32-C6"),
    ("esp32", r"ESP32", ["platform = espressif32", "board = esp32dev", "framework = arduino"], "ESP32"),
    ("rp2350", r"RP2350", ["platform = https://github.com/maxgerhardt/platform-raspberrypi.git", "board = rpipico2",
                           "framework = arduino", "board_build.core = earlephilhower"], "RP2350"),
    ("rp2040", r"RP2040", ["platform = https://github.com/maxgerhardt/platform-raspberrypi.git", "board = pico",
                           "framework = arduino", "board_build.core = earlephilhower"], "RP2040"),
    ("stm32", r"STM32[A-Z]\d", ["platform = ststm32", "framework = arduino"], "STM32"),
    ("atmega328", r"ATMEGA328", ["platform = atmelavr", "board = ATmega328P", "framework = arduino",
                                 "board_build.f_cpu = 16000000L", "upload_protocol = usbasp"], "ATmega328P"),
    ("atmega32u4", r"ATMEGA32U4", ["platform = atmelavr", "board = leonardo", "framework = arduino"], "ATmega32U4"),
    ("attiny85", r"ATTINY(25|45|85)", ["platform = atmelavr", "board = attiny85", "framework = arduino",
                                       "upload_protocol = usbasp"], "ATtiny85"),
]
STM32_BOARDS = {"STM32F103C8": "bluepill_f103c8", "STM32F103CB": "bluepill_f103c8_128k",
                "STM32F401CC": "blackpill_f401cc", "STM32F411CE": "blackpill_f411ce",
                "STM32F030F4": "genericSTM32F030F4", "STM32G030F6": "genericSTM32G030F6",
                "STM32G431CB": "genericSTM32G431CB", "STM32F072CB": "genericSTM32F072CB",
                "STM32L432KC": "nucleo_l432kc", "STM32F405RG": "genericSTM32F405RG"}
M328 = {**{f"PD{i}": i for i in range(8)}, **{f"PB{i}": 8 + i for i in range(6)},
        **{f"PC{i}": f"A{i}" for i in range(6)}, "ADC6": "A6", "ADC7": "A7"}
M32U4 = {"PD2": 0, "PD3": 1, "PD1": 2, "PD0": 3, "PD4": 4, "PC6": 5, "PD7": 6, "PE6": 7, "PB4": 8, "PB5": 9,
         "PB6": 10, "PB7": 11, "PD6": 12, "PC7": 13, "PB3": 14, "PB1": 15, "PB2": 16, "PB0": 17,
         "PF7": "A0", "PF6": "A1", "PF5": "A2", "PF4": "A3", "PF1": "A4", "PF0": "A5"}
ESP32_ALIASES = {"SENSOR_VP": 36, "SENSOR_VN": 39, "TXD0": 1, "RXD0": 3, "U0TXD": 1, "U0RXD": 3}
POWER = re.compile(r"^(GND|AGND|DGND|VSS|VDD|VCC|VBUS|VBAT|VIN|\+?\d+V\d*|3V3|5V|\+3V3|\+5V)\w*$", re.I)
SKIP_PIN = re.compile(r"^(EN|CHIP_PU|~?\{?RESET\}?|NRST|RUN|BOOT0|XIN|XOUT|XTAL|OSC|USB_D|QSPI|SWD|SWCLK|TESTEN|VREG|AREF)", re.I)


def detect(spec: dict) -> list[tuple[dict, tuple]]:
    """The microcontrollers on the board, with their family entry."""
    out = []
    for c in spec.get("components") or []:
        text = " ".join(str(c.get(k) or "") for k in ("symbol", "value", "mpn")).upper()
        # a description names the chip only on a chip: "Switch reset from the ESP32" on a diode does not
        if re.match(r"(U|IC|M|MOD)\d", str(c.get("ref") or "").upper()):
            text += " " + str(c.get("description") or "").upper()
        for fam in FAMILIES:
            if re.search(fam[1], text):
                out.append((c, fam))
                break
    return out


def _gpio(family: str, names: list[str]):
    """The Arduino pin for a symbol pin's names, e.g. ['IO4'] -> 4, ['XTAL1/PB6'] -> None."""
    for n in names:
        n = n.strip().strip("~{}")
        if family.startswith("esp32"):
            if n in ESP32_ALIASES and family == "esp32":
                return ESP32_ALIASES[n]
            m = re.fullmatch(r"(?:IO|GPIO)(\d+)", n)
            if m:
                return int(m.group(1))
        elif family.startswith("rp2"):
            m = re.fullmatch(r"GPIO(\d+)(?:_ADC\d)?", n)
            if m:
                return int(m.group(1))
        elif family == "stm32":
            if re.fullmatch(r"P[A-K]\d{1,2}", n):
                return n
        elif family == "atmega328":
            if n in M328:
                return M328[n]
        elif family == "atmega32u4":
            if n in M32U4:
                return M32U4[n]
        elif family == "attiny85":
            m = re.fullmatch(r"PB([0-5])", n)
            if m:
                return int(m.group(1))
    return None


def _role(net: str, others: list[dict]) -> tuple[str, str]:
    """(role, macro suffix) guessed from the net name and what else is on the net."""
    up = net.upper()
    kinds = {re.match(r"[A-Z]+", o["ref"]).group(0) for o in others if re.match(r"[A-Z]+", o["ref"])}
    vals = " ".join(str(o.get("value") or "") + " " + str(o.get("footprint") or "") for o in others).upper()
    for pat, role in ((r"\bSDA\b|_SDA|SDA_|^SDA", "i2c_sda"), (r"\bSCL\b|_SCL|SCL_|^SCL", "i2c_scl"),
                      (r"MOSI|COPI|SDO", "spi_mosi"), (r"MISO|CIPO|SDI", "spi_miso"), (r"SCK|SCLK", "spi_sck"),
                      (r"\bCS\b|_CS|CS_|NSS|^SS", "spi_cs"), (r"TX", "uart_tx"), (r"RX", "uart_rx"),
                      (r"WS2812|NEOPIXEL|SK6812|DIN|LED_DATA", "ws2812"), (r"LED", "led"),
                      (r"BTN|BUTTON|SW|KEY", "button"), (r"ADC|AIN|SENSE|VMEAS|BAT", "analog"),
                      (r"PWM|FAN|MOTOR|BUZZ", "pwm"), (r"INT|IRQ|ALERT|DRDY", "interrupt")):
        if re.search(pat, up):
            return role, ""
    if "WS2812" in vals or "SK6812" in vals:
        return "ws2812", ""
    if "D" in kinds and "LED" in vals:
        return "led", ""
    if "SW" in kinds:
        return "button", ""
    return "gpio", ""


def pin_map(spec: dict, mcu: dict, family: str) -> tuple[list[dict], list[str]]:
    notes = []
    node = schematic.load_symbol(mcu["symbol"]) if mcu.get("symbol") else None
    if node is None:
        return [], [f"{mcu['ref']} has no KiCad symbol with pin names ({mcu.get('symbol') or 'none given'}), "
                    "so pins.h could not be written."]
    names: dict[str, list[str]] = {}
    for plist in schematic.symbol_pins(node).values():
        for p in plist:
            names.setdefault(str(p["number"]), []).extend(str(p["name"]).split("/"))
    comps = {c["ref"]: c for c in spec.get("components") or []}
    rows, used = [], set()
    for n in spec.get("nets") or []:
        if POWER.match(n["name"]) or n.get("class") == "power":
            continue
        mine = [p.split(".", 1)[1] for p in n["pins"] if p.split(".", 1)[0] == mcu["ref"]]
        if not mine:
            continue
        others = [comps[r] for r in {p.split(".", 1)[0] for p in n["pins"]} - {mcu["ref"]} if r in comps]
        for pad in mine:
            pn = names.get(pad, [])
            if any(SKIP_PIN.match(x) for x in pn):
                continue
            g = _gpio(family, pn)
            if g is None:
                if pn:
                    notes.append(f"{n['name']} is on {mcu['ref']} pad {pad} ({'/'.join(pn)}), which is not a GPIO.")
                continue
            macro = "PIN_" + (re.sub(r"[^A-Z0-9]+", "_", n["name"].upper().lstrip("/")).strip("_") or f"PAD{pad}")
            if macro[4:5].isdigit():
                macro = "PIN_N" + macro[4:]
            base, i = macro, 2
            while macro in used:
                macro, i = f"{base}_{i}", i + 1
            used.add(macro)
            role, _ = _role(n["name"], others)
            rows.append({"macro": macro, "gpio": g, "pad": pad, "pin_name": "/".join(pn), "net": n["name"],
                         "role": role, "connects": sorted(o["ref"] for o in others)})
    rows.sort(key=lambda r: (str(type(r["gpio"])), r["gpio"] if isinstance(r["gpio"], int) else str(r["gpio"])))
    return rows, notes


def _ini(fam: tuple, mcu: dict) -> str:
    lines = list(fam[2])
    if fam[0] == "stm32":
        text = " ".join(str(mcu.get(k) or "") for k in ("symbol", "value", "mpn")).upper()
        m = re.search(r"STM32[A-Z]\d{3}[A-Z0-9]{2}", text)
        board = STM32_BOARDS.get(m.group(0)[:11]) if m else None
        lines.insert(1, f"board = {board or 'genericSTM32F103C8'}" + ("" if board else "   ; change to your chip's board id"))
        lines.append("upload_protocol = stlink")
    return ("; Starter firmware generated by WholeBoard. Build and upload with PlatformIO:\n"
            ";   pio run -t upload        then   pio device monitor\n\n"
            f"[env:{fam[0]}]\n" + "\n".join(lines) + "\nmonitor_speed = 115200\n")


def _pins_h(rows: list[dict], fam: tuple, mcu: dict, spec: dict) -> str:
    out = [f"// Pin map for {spec.get('name', 'board')}: {mcu['ref']} {mcu.get('value', '')} ({fam[3]}).",
           "// Generated by WholeBoard from the schematic; each pin is named after its net.", "#pragma once", ""]
    w = max([len(r["macro"]) for r in rows] + [8])
    for r in rows:
        val = r["gpio"] if isinstance(r["gpio"], int) else r["gpio"]
        out.append(f"#define {r['macro']:<{w}} {val:<5}  // pad {r['pad']} {r['pin_name']}, {r['role']}"
                   + (f", to {', '.join(r['connects'][:5])}" if r["connects"] else ""))
    return "\n".join(out) + "\n"


def _main_cpp(rows: list[dict], fam: tuple) -> str:
    by = lambda role: [r for r in rows if r["role"] == role]
    leds, buttons, analog = by("led") + by("pwm"), by("button"), by("analog")
    sda, scl = (by("i2c_sda") or [None])[0], (by("i2c_scl") or [None])[0]
    tx, rx = (by("uart_tx") or [None])[0], (by("uart_rx") or [None])[0]
    pixels = by("ws2812")
    inc = ["#include <Arduino.h>", '#include "pins.h"']
    if sda and scl:
        inc.append("#include <Wire.h>")
    setup = ["  Serial.begin(115200);", "  delay(500);", '  Serial.println("Hello from WholeBoard");']
    for r in leds:
        setup.append(f"  pinMode({r['macro']}, OUTPUT);")
    for r in buttons:
        setup.append(f"  pinMode({r['macro']}, INPUT_PULLUP);")
    if sda and scl:
        if fam[0].startswith("esp32"):
            setup.append(f"  Wire.begin({sda['macro']}, {scl['macro']});")
        elif fam[0].startswith("rp2"):
            setup += [f"  Wire.setSDA({sda['macro']});", f"  Wire.setSCL({scl['macro']});", "  Wire.begin();"]
        else:
            setup.append(f"  Wire.begin();  // the hardware I2C pins: {sda['macro']}, {scl['macro']}")
        setup += ["", "  // List the I2C devices on the bus.", "  for (uint8_t a = 1; a < 127; a++) {",
                  "    Wire.beginTransmission(a);", "    if (Wire.endTransmission() == 0) {",
                  '      Serial.print("I2C device at 0x"); Serial.println(a, HEX);', "    }", "  }"]
    if tx and rx and fam[0].startswith("esp32"):
        setup.append(f"  Serial1.begin(115200, SERIAL_8N1, {rx['macro']}, {tx['macro']});")
    if pixels:
        setup.append(f"  // {pixels[0]['macro']} drives addressable LEDs: add the Adafruit NeoPixel library to lib_deps.")
    loop = ["  static uint32_t last = 0;", "  static bool on = false;", "  if (millis() - last >= 500) {",
            "    last = millis();", "    on = !on;"]
    loop += [f"    digitalWrite({r['macro']}, on);" for r in leds]
    loop += [f'    Serial.print("{r["macro"]} = "); Serial.println(analogRead({r["macro"]}));' for r in analog]
    loop.append("  }")
    for r in buttons:
        loop += [f"  if (digitalRead({r['macro']}) == LOW) {{", f'    Serial.println("{r["net"]} pressed");',
                 "    delay(200);", "  }"]
    return ("\n".join(inc) + "\n\n// Starter code: blinks the LEDs, reports buttons and analog inputs, and scans I2C.\n"
            "void setup() {\n" + "\n".join(setup) + "\n}\n\nvoid loop() {\n" + "\n".join(loop) + "\n}\n")


def generate(spec: dict, out_dir: Path) -> dict | None:
    """Write the PlatformIO project for the first microcontroller. None when there is none."""
    found = detect(spec)
    if not found:
        return None
    mcu, fam = found[0]
    rows, notes = pin_map(spec, mcu, fam[0])
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "src").mkdir(parents=True)
    (out_dir / "include").mkdir()
    (out_dir / "platformio.ini").write_text(_ini(fam, mcu))
    (out_dir / "include" / "pins.h").write_text(_pins_h(rows, fam, mcu, spec))
    (out_dir / "src" / "main.cpp").write_text(_main_cpp(rows, fam))
    table = "\n".join(f"| {r['macro']} | {r['gpio']} | {r['pad']} | {r['pin_name']} | {r['net']} | {r['role']} | "
                      f"{', '.join(r['connects'])} |" for r in rows)
    (out_dir / "README.md").write_text(
        f"# {spec.get('name', 'board')} firmware\n\nStarter firmware for {mcu['ref']} ({fam[3]}), made by WholeBoard.\n\n"
        "Install PlatformIO (VS Code extension or `pip install platformio`), then `pio run -t upload` and "
        "`pio device monitor`.\n\n| Macro | Pin | Pad | Symbol pin | Net | Use | Connects to |\n|---|---|---|---|---|---|---|\n"
        + table + "\n" + ("\n" + "\n".join("- " + n for n in notes) + "\n" if notes else ""))
    if len(found) > 1:
        notes.append(f"Firmware is for {mcu['ref']}; the board also has " + ", ".join(c["ref"] for c, _ in found[1:]) + ".")
    return {"mcu": mcu["ref"], "value": mcu.get("value"), "family": fam[3], "env": fam[0], "pins": rows,
            "notes": notes, "dir": str(out_dir)}
