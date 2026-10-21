"""Proven building blocks: tested sub-circuits the AI drops into a design whole.

A design spec may list blocks next to its own components:

  "blocks": [
    {"block": "usb_c_power", "name": "USB", "connect": {"VBUS": "VBUS", "GND": "GND"},
     "placement": {"J1": {"x_mm": 4, "y_mm": 12, "rotation": 270, "locked": true}}},
    {"block": "esp32c3_wroom02", "name": "MCU", "connect": {"IO4": "SDA", "IO5": "SCL"}}
  ]

`connect` maps the block's ports to nets of the design (a port left out keeps its default net,
or is left unconnected when it has none). `options` turns optional parts on or off, `parts`
overrides fields of a block part ({"D1": {"lcsc": "C2297", "value": "Green"}}) and `placement`
places block parts by their block reference. Expansion gives every block part a free reference
in the design and names the block's inner nets "<name>_<net>".

Pins in a block are "REF.PAD" or "REF:PIN_NAME" (every pad of that symbol pin name).
More blocks can be added as JSON files in ~/.wholeboard/blocks/.
"""

from __future__ import annotations

import copy
import functools
import json
import re

from . import config

R_FP, C_FP = "Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric"
C0805_FP = "Capacitor_SMD:C_0805_2012Metric"
USB_C_FP = "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12"


def _r(ref: str, value: str, lcsc: str, desc: str = "", **kw) -> dict:
    return {"ref": ref, "value": value, "footprint": R_FP, "symbol": "Device:R", "lcsc": lcsc,
            "description": desc or f"{value} resistor", **kw}


def _c(ref: str, value: str, lcsc: str, desc: str = "", fp: str = C_FP, **kw) -> dict:
    return {"ref": ref, "value": value, "footprint": fp, "symbol": "Device:C", "lcsc": lcsc,
            "description": desc or f"{value} capacitor", **kw}


# JLCPCB basic parts, checked October 2026.
R10K, R5K1, R4K7, R2K, R1K, R120, R27 = "C25804", "C23186", "C23162", "C22975", "C21190", "C22787", "C25190"
C100N, C1U, C10U, C22U, C22P, C4U7 = "C14663", "C15849", "C15850", "C45783", "C1653", "C19666"
BUTTON = {"footprint": "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A", "symbol": "Switch:SW_Push", "lcsc": "C318884",
          "value": "TS-1187A"}

POWER = {"power": True}


def _usb_c(ref: str = "J1") -> dict:
    return {"ref": ref, "value": "USB-C", "footprint": USB_C_FP, "symbol": "Connector:USB_C_Receptacle_USB2.0_16P",
            "lcsc": "C165948", "mpn": "TYPE-C-31-M-12", "description": "USB-C 16-pin receptacle (USB 2.0)"}


def _gpio(ref: str, names: list[str], desc: str = "GPIO") -> dict:
    return {n: {"desc": desc, "pins": [f"{ref}:{n}"]} for n in names}


BUILTIN: list[dict] = [
    {
        "id": "usb_c_power", "category": "power input",
        "title": "USB-C power input (5 V, up to 3 A from a USB-C charger)",
        "description": "USB-C receptacle wired as a power sink: 5.1 kΩ pull-downs on CC1/CC2 so USB-C chargers "
                       "turn on 5 V. Data pins unused. Put J1 at a board edge, opening outwards.",
        "components": [_usb_c(), _r("R1", "5.1k", R5K1, "CC1 pull-down"), _r("R2", "5.1k", R5K1, "CC2 pull-down")],
        "ports": {"VBUS": {"desc": "5 V from USB", "default": "VBUS", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"VBUS": ["J1:VBUS"], "GND": ["J1:GND", "J1.SH", "R1.2", "R2.2"],
                 "CC1": ["J1.A5", "R1.1"], "CC2": ["J1.B5", "R2.1"]},
        "no_connect": {"J1": ["A6", "A7", "A8", "B6", "B7", "B8"]},
        "sim": {"sources": [{"net": "VBUS", "volts": 5, "kind": "usb", "max_ma": 500}]},
    },
    {
        "id": "usb_c_data", "category": "power input",
        "title": "USB-C with USB 2.0 data (power + D+/D-, ESD protected)",
        "description": "USB-C receptacle as a USB 2.0 device: CC pull-downs, both D+/D- rows joined, and a "
                       "USBLC6-2SC6 ESD protector on the data lines (option esd). Connect USB_DP/USB_DN to the MCU "
                       "(through its series resistors if the MCU block has none). Put J1 at a board edge.",
        "components": [_usb_c(), _r("R1", "5.1k", R5K1, "CC1 pull-down"), _r("R2", "5.1k", R5K1, "CC2 pull-down"),
                       {"ref": "U1", "value": "USBLC6-2SC6", "footprint": "Package_TO_SOT_SMD:SOT-23-6",
                        "symbol": "Power_Protection:USBLC6-2SC6", "lcsc": "C7519", "description": "USB ESD protection",
                        "option": "esd"}],
        "options": {"esd": {"default": True, "desc": "USBLC6-2SC6 ESD protection on D+/D-"}},
        "ports": {"VBUS": {"desc": "5 V from USB", "default": "VBUS", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER},
                  "USB_DP": {"desc": "USB D+", "default": "USB_DP", "pins": ["J1.A6", "J1.B6", "U1.1", "U1.6"]},
                  "USB_DN": {"desc": "USB D-", "default": "USB_DN", "pins": ["J1.A7", "J1.B7", "U1.3", "U1.4"]}},
        "nets": {"VBUS": ["J1:VBUS", "U1.5"], "GND": ["J1:GND", "J1.SH", "R1.2", "R2.2", "U1.2"],
                 "CC1": ["J1.A5", "R1.1"], "CC2": ["J1.B5", "R2.1"]},
        "no_connect": {"J1": ["A8", "B8"]},
        "impedance": [{"nets": ["USB_DP", "USB_DN"], "ohms": 90, "kind": "diff", "max_skew_mm": 1.25}],
        "sim": {"sources": [{"net": "VBUS", "volts": 5, "kind": "usb", "max_ma": 500}]},
    },
    {
        "id": "ldo_ams1117_3v3", "category": "regulator",
        "title": "3.3 V LDO, 800 mA (AMS1117-3.3, SOT-223)",
        "description": "Classic 1 A-class LDO with 10 µF in and 22 µF out. Needs at least 4.5 V in (1.1 V dropout) "
                       "and gets hot: from 5 V it dissipates 1.7 V × load current; keep the load under ~400 mA "
                       "without extra copper. Quiescent 5 mA, so not for batteries.",
        "components": [{"ref": "U1", "value": "AMS1117-3.3", "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
                        "symbol": "Regulator_Linear:AMS1117-3.3", "lcsc": "C6186", "description": "3.3 V 1 A LDO"},
                       _c("C1", "10uF", C10U, "LDO input", C0805_FP), _c("C2", "22uF", C22U, "LDO output", C0805_FP)],
        "ports": {"VIN": {"desc": "Input, 4.5 to 15 V", "required": True, **POWER},
                  "VOUT": {"desc": "3.3 V output", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"VIN": ["U1:VI", "C1.1"], "VOUT": ["U1:VO", "C2.1"], "GND": ["U1:GND", "C1.2", "C2.2"]},
    },
    {
        "id": "ldo_ap2112k_3v3", "category": "regulator",
        "title": "3.3 V LDO, 600 mA, low dropout (AP2112K-3.3, SOT-23-5)",
        "description": "Small LDO with 250 mV dropout and 55 µA quiescent current: good from USB 5 V or a LiPo cell "
                       "for ESP32/RP2040 boards. Enable tied to the input.",
        "components": [{"ref": "U1", "value": "AP2112K-3.3", "footprint": "Package_TO_SOT_SMD:SOT-23-5",
                        "symbol": "Regulator_Linear:AP2112K-3.3", "lcsc": "C51118", "mpn": "AP2112K-3.3TRG1",
                        "description": "3.3 V 600 mA LDO", "no_connect": ["4"]},
                       _c("C1", "1uF", C1U, "LDO input"), _c("C2", "1uF", C1U, "LDO output")],
        "ports": {"VIN": {"desc": "Input, 3.55 to 6 V", "required": True, **POWER},
                  "VOUT": {"desc": "3.3 V output", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"VIN": ["U1:VIN", "U1:EN", "C1.1"], "VOUT": ["U1:VOUT", "C2.1"], "GND": ["U1:GND", "C1.2", "C2.2"]},
    },
    {
        "id": "ldo_xc6206_3v3", "category": "regulator",
        "title": "3.3 V LDO, 200 mA, 1 µA quiescent (XC6206P332MR, SOT-23)",
        "description": "Tiny low-power LDO for sensors and low-power MCUs on batteries. Not enough for Wi-Fi "
                       "modules (they peak at 350 mA). Input up to 6 V.",
        "components": [{"ref": "U1", "value": "XC6206P332MR", "footprint": "Package_TO_SOT_SMD:SOT-23-3",
                        "symbol": "Regulator_Linear:XC6206PxxxMR", "lcsc": "C5446", "description": "3.3 V 200 mA LDO"},
                       _c("C1", "1uF", C1U, "LDO input"), _c("C2", "1uF", C1U, "LDO output")],
        "ports": {"VIN": {"desc": "Input, 3.6 to 6 V", "required": True, **POWER},
                  "VOUT": {"desc": "3.3 V output", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"VIN": ["U1:VI", "C1.1"], "VOUT": ["U1:VO", "C2.1"], "GND": ["U1:GND", "C1.2", "C2.2"]},
    },
    {
        "id": "esp32s3_wroom1", "category": "microcontroller",
        "title": "ESP32-S3 Wi-Fi/Bluetooth module with reset and boot buttons (ESP32-S3-WROOM-1-N16R8)",
        "description": "ESP32-S3-WROOM-1 (16 MB flash, 8 MB octal PSRAM) with 10 µF + 100 nF on 3V3, the EN "
                       "RC delay (10 kΩ, 1 µF), a RESET button on EN and a BOOT button on IO0. Native USB on "
                       "USB_DP/USB_DN (connect to a usb_c_data block). IO35-IO37 are used by the PSRAM. Needs "
                       "3.3 V at 500 mA peak. Place it at a board edge with the antenna end (the module's top, "
                       "away from the pins) overhanging or over a copper-free area.",
        "components": [{"ref": "U1", "value": "ESP32-S3-WROOM-1-N16R8", "footprint": "RF_Module:ESP32-S3-WROOM-1",
                        "symbol": "RF_Module:ESP32-S3-WROOM-1", "lcsc": "C2913202",
                        "description": "ESP32-S3 Wi-Fi/BLE module, 16 MB flash, 8 MB PSRAM",
                        "no_connect": ["28", "29", "30"]},
                       _c("C1", "10uF", C10U, "3V3 bulk", C0805_FP), _c("C2", "100nF", C100N, "3V3 decoupling"),
                       _r("R1", "10k", R10K, "EN pull-up"), _c("C3", "1uF", C1U, "EN delay"),
                       {"ref": "SW1", **BUTTON, "description": "RESET button"},
                       {"ref": "SW2", **BUTTON, "description": "BOOT button (hold while resetting to flash)"}],
        "ports": {"3V3": {"desc": "3.3 V supply, 500 mA peak", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER},
                  "USB_DP": {"desc": "Native USB D+ (IO20)", "pins": ["U1:USB_D+"]},
                  "USB_DN": {"desc": "Native USB D- (IO19)", "pins": ["U1:USB_D-"]},
                  "TXD0": {"desc": "UART0 TX (IO43)", "pins": ["U1:TXD0"]},
                  "RXD0": {"desc": "UART0 RX (IO44)", "pins": ["U1:RXD0"]},
                  **_gpio("U1", ["IO1", "IO2", "IO3", "IO4", "IO5", "IO6", "IO7", "IO8", "IO9", "IO10", "IO11",
                                 "IO12", "IO13", "IO14", "IO15", "IO16", "IO17", "IO18", "IO21", "IO38", "IO39",
                                 "IO40", "IO41", "IO42", "IO45", "IO46", "IO47", "IO48"],
                          "GPIO (IO1-IO10 also ADC1; IO3, IO45, IO46 are strapping pins)")},
        "nets": {"3V3": ["U1:3V3", "C1.1", "C2.1", "R1.1"], "GND": ["U1:GND", "C1.2", "C2.2", "C3.2", "SW1.2", "SW2.2"],
                 "EN": ["U1:EN", "R1.2", "C3.1", "SW1.1"], "BOOT": ["U1:IO0", "SW2.1"]},
    },
    {
        "id": "esp32c3_wroom02", "category": "microcontroller",
        "title": "ESP32-C3 Wi-Fi/Bluetooth module with reset and boot buttons (ESP32-C3-WROOM-02-N4)",
        "description": "ESP32-C3-WROOM-02 (RISC-V, 4 MB flash) with 10 µF + 100 nF on 3V3, the EN RC delay, a "
                       "RESET button, a BOOT button on IO9 and 10 kΩ pull-ups on the strapping pins IO2 and IO8 "
                       "(both still usable as outputs). Native USB on USB_DP (IO19) / USB_DN (IO18). Needs 3.3 V "
                       "at 350 mA peak. Put the antenna end at a board edge, clear of copper.",
        "components": [{"ref": "U1", "value": "ESP32-C3-WROOM-02-N4", "footprint": "RF_Module:ESP32-C3-WROOM-02",
                        "symbol": "RF_Module:ESP32-C3-WROOM-02", "lcsc": "C2934560",
                        "description": "ESP32-C3 Wi-Fi/BLE module, 4 MB flash"},
                       _c("C1", "10uF", C10U, "3V3 bulk", C0805_FP), _c("C2", "100nF", C100N, "3V3 decoupling"),
                       _r("R1", "10k", R10K, "EN pull-up"), _c("C3", "1uF", C1U, "EN delay"),
                       _r("R2", "10k", R10K, "IO2 strapping pull-up"), _r("R3", "10k", R10K, "IO8 strapping pull-up"),
                       {"ref": "SW1", **BUTTON, "description": "RESET button"},
                       {"ref": "SW2", **BUTTON, "description": "BOOT button (hold while resetting to flash)"}],
        "ports": {"3V3": {"desc": "3.3 V supply, 350 mA peak", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER},
                  "USB_DP": {"desc": "Native USB D+ (IO19)", "pins": ["U1:IO19"]},
                  "USB_DN": {"desc": "Native USB D- (IO18)", "pins": ["U1:IO18"]},
                  "IO2": {"desc": "GPIO, ADC (10 kΩ pull-up fitted)", "pins": ["U1:IO2", "R2.2"]},
                  "IO8": {"desc": "GPIO (10 kΩ pull-up fitted; drive LEDs active-low)", "pins": ["U1:IO8", "R3.2"]},
                  **_gpio("U1", ["IO0", "IO1", "IO3", "IO4"], "GPIO, ADC"),
                  **_gpio("U1", ["IO5", "IO6", "IO7", "IO10"], "GPIO"),
                  "IO20": {"desc": "GPIO / UART0 RX", "pins": ["U1:IO20"]},
                  "IO21": {"desc": "GPIO / UART0 TX", "pins": ["U1:IO21"]}},
        "nets": {"3V3": ["U1:3V3", "C1.1", "C2.1", "R1.1", "R2.1", "R3.1"],
                 "GND": ["U1:GND", "C1.2", "C2.2", "C3.2", "SW1.2", "SW2.2"],
                 "EN": ["U1:EN", "R1.2", "C3.1", "SW1.1"], "BOOT": ["U1:IO9", "SW2.1"]},
    },
    {
        "id": "rp2040_min", "category": "microcontroller",
        "title": "RP2040 minimum system: 16 MB flash, 12 MHz crystal, USB, BOOTSEL and RESET",
        "description": "Raspberry Pi RP2040 with W25Q128 QSPI flash, a 12 MHz crystal (1 kΩ series resistor, "
                       "22 pF load caps), 100 nF on every supply pin, the internal 1.1 V core regulator with its "
                       "1 µF caps, 27 Ω series resistors on USB D+/D-, a BOOTSEL button (pulls flash CS low "
                       "through 1 kΩ) and a RESET button on RUN. Needs 3.3 V at ~50 mA. Keep the crystal and "
                       "flash next to the RP2040.",
        "components": [{"ref": "U1", "value": "RP2040", "footprint": "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm",
                        "symbol": "MCU_RaspberryPi:RP2040", "lcsc": "C2040", "description": "RP2040 dual-core MCU"},
                       {"ref": "U2", "value": "W25Q128JVSIQ", "footprint": "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm",
                        "symbol": "Memory_Flash:W25Q128JVS", "lcsc": "C97521", "description": "16 MB QSPI flash"},
                       {"ref": "Y1", "value": "12MHz", "footprint": "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
                        "symbol": "Device:Crystal_GND24", "lcsc": "C9002", "description": "12 MHz crystal"},
                       _c("C1", "22pF", C22P, "crystal load"), _c("C2", "22pF", C22P, "crystal load"),
                       _r("R1", "1k", R1K, "crystal series resistor"),
                       *[_c(f"C{i}", "100nF", C100N, "IOVDD decoupling") for i in range(3, 9)],
                       _c("C9", "100nF", C100N, "DVDD decoupling"), _c("C10", "100nF", C100N, "DVDD decoupling"),
                       _c("C11", "100nF", C100N, "USB_VDD decoupling"), _c("C12", "100nF", C100N, "ADC_AVDD decoupling"),
                       _c("C13", "1uF", C1U, "VREG_VIN"), _c("C14", "1uF", C1U, "VREG_VOUT (1.1 V core)"),
                       _c("C15", "10uF", C10U, "3V3 bulk", C0805_FP), _c("C16", "100nF", C100N, "flash decoupling"),
                       _r("R2", "27", R27, "USB D+ series"), _r("R3", "27", R27, "USB D- series"),
                       _r("R4", "1k", R1K, "BOOTSEL"), _r("R5", "10k", R10K, "RUN pull-up"),
                       _r("R6", "10k", R10K, "flash CS pull-up"),
                       {"ref": "SW1", **BUTTON, "description": "BOOTSEL button (hold while plugging in USB)"},
                       {"ref": "SW2", **BUTTON, "description": "RESET button"}],
        "ports": {"3V3": {"desc": "3.3 V supply, ~50 mA", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER},
                  "USB_DP": {"desc": "USB D+ (after the 27 Ω resistor)", "pins": ["R2.2"]},
                  "USB_DN": {"desc": "USB D- (after the 27 Ω resistor)", "pins": ["R3.2"]},
                  "SWCLK": {"desc": "SWD debug clock", "pins": ["U1:SWCLK"]},
                  "SWDIO": {"desc": "SWD debug data", "pins": ["U1:SWDIO"]},
                  **_gpio("U1", [f"GPIO{i}" for i in range(0, 26)], "GPIO"),
                  **_gpio("U1", [f"GPIO{i}" for i in range(26, 30)], "GPIO / ADC")},
        "nets": {"3V3": ["U1:IOVDD", "U1:USB_VDD", "U1:ADC_AVDD", "U1:VREG_VIN", "U2:VCC", "R5.1", "R6.1",
                         *[f"C{i}.1" for i in (3, 4, 5, 6, 7, 8, 11, 12, 13, 15, 16)]],
                 "1V1": ["U1:DVDD", "U1:VREG_VOUT", "C9.1", "C10.1", "C14.1"],
                 "GND": ["U1:GND", "U1:TESTEN", "U2:GND", "Y1.2", "Y1.4", "C1.2", "C2.2", "SW1.2", "SW2.2",
                         *[f"C{i}.2" for i in range(3, 17)]],
                 "XIN": ["U1:XIN", "Y1.1", "C1.1"], "XOUT": ["U1:XOUT", "R1.1"], "XTAL": ["R1.2", "Y1.3", "C2.1"],
                 "QSPI_SS": ["U1:QSPI_SS", "U2:CS", "R4.1", "R6.2"], "BOOTSEL": ["R4.2", "SW1.1"],
                 "QSPI_SCLK": ["U1:QSPI_SCLK", "U2:CLK"], "QSPI_SD0": ["U1:QSPI_SD0", "U2:IO_0"],
                 "QSPI_SD1": ["U1:QSPI_SD1", "U2:IO_1"], "QSPI_SD2": ["U1:QSPI_SD2", "U2:IO_2"],
                 "QSPI_SD3": ["U1:QSPI_SD3", "U2:IO_3"],
                 "RUN": ["U1:RUN", "R5.2", "SW2.1"], "USB_D_P": ["U1:USB_DP", "R2.1"], "USB_D_N": ["U1:USB_DM", "R3.1"]},
    },
    {
        "id": "lipo_charger_tp4056", "category": "battery",
        "title": "Single-cell LiPo/Li-ion charger, 580 mA (TP4056) with charge/done LEDs",
        "description": "TP4056 linear charger: 4.2 V charge voltage, 2 kΩ PROG for ~580 mA (use 1.2 kΩ for 1 A on "
                       "a big cell; it gets warm), red CHARGING and green DONE LEDs, battery temperature sensing "
                       "off. VIN 4.5-5.5 V (USB). Charge current must be under the cell's 1C rating.",
        "components": [{"ref": "U1", "value": "TP4056", "footprint": "Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm_ThermalVias",
                        "symbol": "Battery_Management:TP4056-42-ESOP8", "lcsc": "C16581", "mpn": "TP4056-42-ESOP8",
                        "description": "1 A Li-ion charger"},
                       _r("R1", "2k", R2K, "PROG: ~580 mA charge current"),
                       _c("C1", "10uF", C10U, "charger input", C0805_FP), _c("C2", "10uF", C10U, "battery", C0805_FP),
                       _r("R2", "1k", R1K, "CHRG LED"), _r("R3", "1k", R1K, "STDBY LED"),
                       {"ref": "D1", "value": "Red", "footprint": "LED_SMD:LED_0603_1608Metric", "symbol": "Device:LED",
                        "lcsc": "C2286", "description": "Charging LED"},
                       {"ref": "D2", "value": "Green", "footprint": "LED_SMD:LED_0805_2012Metric", "symbol": "Device:LED",
                        "lcsc": "C2297", "description": "Charged LED"}],
        "ports": {"VIN": {"desc": "Charger input, 4.5 to 5.5 V", "required": True, **POWER},
                  "BAT": {"desc": "Battery + (cell 3.0-4.2 V)", "default": "VBAT", **POWER},
                  "GND": {"desc": "Ground / battery -", "default": "GND", **POWER}},
        "nets": {"VIN": ["U1:V_CC", "U1:CE", "C1.1", "R2.1", "R3.1"], "BAT": ["U1:BAT", "C2.1"],
                 "GND": ["U1:GND", "U1:TEMP", "U1:EPAD", "R1.2", "C1.2", "C2.2"], "PROG": ["U1:PROG", "R1.1"],
                 "LED_CHRG_A": ["R2.2", "D1.2"], "CHRG": ["U1:CHRG", "D1.1"],
                 "LED_STDBY_A": ["R3.2", "D2.2"], "STDBY": ["U1:STDBY", "D2.1"]},
        "sim": {"sources": [{"net": "BAT", "volts": 3.7, "kind": "battery", "capacity_mah": 1000}]},
    },
    {
        "id": "can_sn65hvd230", "category": "interface",
        "title": "CAN bus transceiver, 3.3 V (SN65HVD230) with 120 Ω termination",
        "description": "SN65HVD230 at 3.3 V, slope control off (Rs to GND, up to 1 Mbit/s), 100 nF decoupling and "
                       "an optional 120 Ω terminator between CANH and CANL (fit it only at the two ends of the "
                       "bus). TXD goes to the MCU's CAN TX pin, RXD to its CAN RX pin.",
        "components": [{"ref": "U1", "value": "SN65HVD230", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                        "symbol": "Interface_CAN_LIN:SN65HVD230", "lcsc": "C12084", "mpn": "SN65HVD230DR",
                        "description": "3.3 V CAN transceiver", "no_connect": ["5"]},
                       _c("C1", "100nF", C100N, "transceiver decoupling"),
                       _r("R1", "120", R120, "CAN bus termination", option="termination")],
        "options": {"termination": {"default": True, "desc": "120 Ω between CANH and CANL"}},
        "ports": {"TXD": {"desc": "From the MCU's CAN TX", "required": True, "pins": ["U1:D"]},
                  "RXD": {"desc": "To the MCU's CAN RX", "required": True, "pins": ["U1:R"]},
                  "CANH": {"desc": "CAN high (to the bus connector)", "default": "CANH", "pins": ["U1:CANH", "R1.1"]},
                  "CANL": {"desc": "CAN low (to the bus connector)", "default": "CANL", "pins": ["U1:CANL", "R1.2"]},
                  "3V3": {"desc": "3.3 V supply", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"3V3": ["U1:VCC", "C1.1"], "GND": ["U1:GND", "U1:Rs", "C1.2"]},
    },
    {
        "id": "rs485_sp3485", "category": "interface",
        "title": "RS-485 transceiver, 3.3 V half duplex (SP3485) with 120 Ω termination",
        "description": "SP3485 at 3.3 V up to 10 Mbit/s. DE and /RE are joined: drive DE_RE high to transmit, low "
                       "to receive. DI is the MCU's UART TX, RO its UART RX. Optional 120 Ω terminator between "
                       "A and B (fit it at the two ends of the bus).",
        "components": [{"ref": "U1", "value": "SP3485EN", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                        "symbol": "Interface_UART:SP3485EN", "lcsc": "C8963", "mpn": "SP3485EN-L/TR",
                        "description": "3.3 V RS-485 transceiver"},
                       _c("C1", "100nF", C100N, "transceiver decoupling"),
                       _r("R1", "120", R120, "RS-485 termination", option="termination")],
        "options": {"termination": {"default": True, "desc": "120 Ω between A and B"}},
        "ports": {"DI": {"desc": "From the MCU's UART TX", "required": True, "pins": ["U1:DI"]},
                  "RO": {"desc": "To the MCU's UART RX", "required": True, "pins": ["U1:RO"]},
                  "DE_RE": {"desc": "Direction: high transmits, low receives", "required": True,
                            "pins": ["U1:DE", "U1:RE"]},
                  "A": {"desc": "RS-485 A (non-inverting)", "default": "RS485_A", "pins": ["U1:A", "R1.1"]},
                  "B": {"desc": "RS-485 B (inverting)", "default": "RS485_B", "pins": ["U1:B", "R1.2"]},
                  "3V3": {"desc": "3.3 V supply", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"3V3": ["U1:VCC", "C1.1"], "GND": ["U1:GND", "C1.2"]},
    },
    {
        "id": "qwiic_i2c", "category": "connector",
        "title": "Qwiic / STEMMA QT I²C connector (JST SH 4-pin) with pull-ups",
        "description": "JST SH 1 mm 4-pin socket with the SparkFun Qwiic pinout (GND, 3V3, SDA, SCL) and 4.7 kΩ "
                       "pull-ups on SDA/SCL (option pullups; fit them once per bus). Put J1 at a board edge.",
        "components": [{"ref": "J1", "value": "Qwiic", "footprint": "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
                        "symbol": "Connector_Generic:Conn_01x04", "lcsc": "C160404", "mpn": "SM04B-SRSS-TB",
                        "description": "Qwiic I2C connector"},
                       _r("R1", "4.7k", R4K7, "SDA pull-up", option="pullups"),
                       _r("R2", "4.7k", R4K7, "SCL pull-up", option="pullups")],
        "options": {"pullups": {"default": True, "desc": "4.7 kΩ pull-ups on SDA and SCL"}},
        "ports": {"SDA": {"desc": "I²C data", "default": "SDA", "pins": ["J1.3", "R1.2"]},
                  "SCL": {"desc": "I²C clock", "default": "SCL", "pins": ["J1.4", "R2.2"]},
                  "3V3": {"desc": "3.3 V to the I²C devices", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"3V3": ["J1.2", "R1.1", "R2.1"], "GND": ["J1.1", "J1.MP"]},
    },
    {
        "id": "led_indicator", "category": "indicator",
        "title": "Indicator LED with series resistor (red 0603, 1 kΩ)",
        "description": "LED from IN to GND through 1 kΩ: about 1.3 mA from a 3.3 V pin, 3 mA from 5 V. For other "
                       "colours override D1, e.g. parts {\"D1\": {\"lcsc\": \"C2297\", \"value\": \"Green\", "
                       "\"footprint\": \"LED_SMD:LED_0805_2012Metric\"}} and lower R1 to 220 Ω (C22962) for "
                       "green/blue/white at 3.3 V.",
        "components": [_r("R1", "1k", R1K, "LED series resistor"),
                       {"ref": "D1", "value": "Red", "footprint": "LED_SMD:LED_0603_1608Metric", "symbol": "Device:LED",
                        "lcsc": "C2286", "description": "Indicator LED"}],
        "ports": {"IN": {"desc": "Drive high to light (an MCU pin or a supply rail)", "required": True, "pins": ["R1.1"]},
                  "GND": {"desc": "Ground", "default": "GND", "pins": ["D1.1"], **POWER}},
        "nets": {"LED_A": ["R1.2", "D1.2"]},
    },
    {
        "id": "button", "category": "input",
        "title": "Push button to GND (TS-1187A) with optional pull-up and debounce cap",
        "description": "Tactile switch from SIG to GND. Use the MCU's internal pull-up, or turn on the pullup "
                       "option (10 kΩ to 3V3). Option debounce adds 100 nF across the switch.",
        "components": [{"ref": "SW1", **BUTTON, "description": "Push button"},
                       _r("R1", "10k", R10K, "pull-up", option="pullup"),
                       _c("C1", "100nF", C100N, "debounce", option="debounce")],
        "options": {"pullup": {"default": False, "desc": "10 kΩ pull-up to 3V3"},
                    "debounce": {"default": False, "desc": "100 nF across the switch"}},
        "ports": {"SIG": {"desc": "To the MCU input (low when pressed)", "required": True,
                          "pins": ["SW1.1", "R1.2", "C1.1"]},
                  "3V3": {"desc": "Pull-up supply (only with the pullup option)", "default": "+3V3",
                          "pins": ["R1.1"], **POWER},
                  "GND": {"desc": "Ground", "default": "GND", "pins": ["SW1.2", "C1.2"], **POWER}},
        "nets": {},
    },
    {
        "id": "usb_uart_ch340c", "category": "interface",
        "title": "USB to UART bridge at 3.3 V (CH340C, no crystal needed)",
        "description": "CH340C with its internal oscillator, run from 3.3 V (V3 tied to VCC). TXD goes to the MCU's "
                       "RX and RXD to its TX. DTR/RTS can drive an auto-reset circuit. Use with MCUs without native "
                       "USB (classic ESP32, ATmega, STM32 without USB).",
        "components": [{"ref": "U1", "value": "CH340C", "footprint": "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
                        "symbol": "Interface_USB:CH340C", "lcsc": "C84681", "description": "USB to UART bridge",
                        "no_connect": ["7", "8", "9", "10", "11", "12", "15"]},
                       _c("C1", "100nF", C100N, "CH340C decoupling")],
        "ports": {"USB_DP": {"desc": "USB D+", "default": "USB_DP", "pins": ["U1:UD+"]},
                  "USB_DN": {"desc": "USB D-", "default": "USB_DN", "pins": ["U1:UD-"]},
                  "TXD": {"desc": "UART out: to the MCU's RX", "required": True, "pins": ["U1:TXD"]},
                  "RXD": {"desc": "UART in: from the MCU's TX", "required": True, "pins": ["U1:RXD"]},
                  "DTR": {"desc": "DTR (active low), for auto-reset", "pins": ["U1:DTR"]},
                  "RTS": {"desc": "RTS (active low), for auto-reset", "pins": ["U1:RTS"]},
                  "3V3": {"desc": "3.3 V supply", "default": "+3V3", **POWER},
                  "GND": {"desc": "Ground", "default": "GND", **POWER}},
        "nets": {"3V3": ["U1:VCC", "U1:V3", "C1.1"], "GND": ["U1:GND", "C1.2"]},
    },
]

USER_DIR = config.APP_DIR / "blocks"


def catalogue() -> dict[str, dict]:
    """Every block by id: the built-in ones and the user's JSON files."""
    out = {b["id"]: b for b in BUILTIN}
    if USER_DIR.is_dir():
        for f in sorted(USER_DIR.glob("*.json")):
            try:
                b = json.loads(f.read_text())
                if isinstance(b, dict) and b.get("id"):
                    out[str(b["id"])] = {**b, "user": True}
            except (OSError, ValueError):
                continue
    return out


def listing() -> list[dict]:
    return [{"id": b["id"], "category": b.get("category", ""), "title": b.get("title", ""),
             "ports": sorted(b.get("ports") or {}), "user": bool(b.get("user"))} for b in catalogue().values()]


def info(block_id: str) -> dict | None:
    """A block as the AI sees it: what it is, its ports and options, and an example entry."""
    b = catalogue().get(block_id)
    if not b:
        return None
    ports = {k: {kk: v[kk] for kk in ("desc", "default", "required", "power") if kk in v}
             for k, v in (b.get("ports") or {}).items()}
    example = {"block": b["id"], "name": re.sub(r"[^A-Z]", "", b["id"].upper())[:4] or "B1",
               "connect": {k: v.get("default") or f"<net for {k}>" for k, v in (b.get("ports") or {}).items()
                           if v.get("required") or v.get("default")}}
    return {"id": b["id"], "title": b.get("title"), "description": b.get("description"),
            "ports": ports, "options": b.get("options") or {},
            "parts": [{k: c.get(k) for k in ("ref", "value", "footprint", "lcsc", "description", "option") if c.get(k)}
                      for c in b.get("components") or []],
            "example": example}


@functools.lru_cache(maxsize=256)
def _pin_names(symbol: str) -> tuple[tuple[str, str], ...]:
    from .kicad import schematic
    try:
        s = schematic.symbol_info(symbol)
    except Exception:  # noqa: BLE001
        return ()
    return tuple((str(p["number"]), str(p["name"])) for p in (s or {}).get("pins") or [])


def _norm(name: str) -> str:
    return re.sub(r"[~{}_\s]", "", name).upper()


def _resolve(pin: str, comps: dict[str, dict]) -> tuple[list[str], str | None]:
    """'U1.3' or 'U1:IOVDD' -> ['U1.3'] / every pad of that pin name."""
    if ":" in pin:
        ref, name = pin.split(":", 1)
        c = comps.get(ref)
        if not c:
            return [], f"unknown part {ref}"
        want = _norm(name)
        pads = [num for num, nm in _pin_names(c.get("symbol") or "")
                if _norm(nm) == want or want in [_norm(x) for x in nm.split("/")]]
        if not pads:
            return [], f"{ref} ({c.get('symbol')}) has no pin named {name}"
        return [f"{ref}.{p}" for p in dict.fromkeys(pads)], None
    if "." in pin:
        return [pin], None
    return [], f"pin {pin!r} must be REF.PAD or REF:NAME"


def _next_ref(prefix: str, used: set[str]) -> str:
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    used.add(f"{prefix}{n}")
    return f"{prefix}{n}"


def expand(spec: dict) -> dict:
    """Replace spec["blocks"] with the blocks' parts and nets. Errors go in spec["block_errors"]."""
    entries = spec.get("blocks")
    if not entries:
        return spec
    spec = dict(spec)
    spec.pop("blocks")
    comps = [dict(c) for c in spec.get("components") or []]
    nets = [dict(n, pins=list(n.get("pins") or [])) for n in spec.get("nets") or []]
    by_name = {n.get("name"): n for n in nets}
    used = {str(c.get("ref")) for c in comps}
    errors: list[str] = list(spec.get("block_errors") or [])
    sim = dict(spec.get("simulation") or {})
    impedance = list(spec.get("impedance") or [])
    record = list(spec.get("blocks_used") or [])
    cat = catalogue()
    names_seen: set[str] = set()
    block_sources: list[dict] = []

    for i, entry in enumerate(entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            errors.append(f"blocks[{i}] must be an object")
            continue
        bid = str(entry.get("block") or entry.get("id") or "")
        b = cat.get(bid)
        if not b:
            errors.append(f"blocks[{i}]: unknown block {bid!r}; list_blocks shows the ones available")
            continue
        name = re.sub(r"[^A-Za-z0-9]+", "_", str(entry.get("name") or f"{bid.split('_')[0].upper()}{i + 1}")).strip("_")
        if name in names_seen:
            errors.append(f"blocks[{i}]: two blocks are named {name}; give each a different name")
            continue
        names_seen.add(name)
        opts = {k: bool(entry.get("options", {}).get(k, v.get("default", True)))
                for k, v in (b.get("options") or {}).items()}
        for k in entry.get("options") or {}:
            if k not in opts:
                errors.append(f"block {name} ({bid}): unknown option {k!r}; it has {', '.join(opts) or 'none'}")
        local = {c["ref"]: copy.deepcopy(c) for c in b.get("components") or []
                 if not c.get("option") or opts.get(c["option"], True)}
        for ref, over in (entry.get("parts") or {}).items():
            if ref not in local:
                errors.append(f"block {name}: parts override for {ref}, which the block does not have")
            elif isinstance(over, dict):
                local[ref].update({k: v for k, v in over.items() if k not in ("ref", "option")})
        rename = {ref: _next_ref(re.match(r"[A-Z_]+", ref)[0], used) for ref in local}
        connect = {str(k): str(v) for k, v in (entry.get("connect") or {}).items() if v}
        ports = b.get("ports") or {}
        for k in connect:
            if k not in ports:
                errors.append(f"block {name} ({bid}): no port {k!r}; its ports are {', '.join(ports)}")

        def pads(pins: list[str]) -> list[str]:
            out = []
            for pin in pins:
                ref = re.split(r"[.:]", pin, maxsplit=1)[0]
                if ref not in local:      # an optional part that is left out
                    continue
                got, err = _resolve(pin, local)
                if err:
                    errors.append(f"block {bid}: {err}")
                out += got
            return out

        def add_net(net: str, pins: list[str], power: bool) -> None:
            pins = [f"{rename[p.split('.')[0]]}.{p.split('.', 1)[1]}" for p in pins]
            n = by_name.get(net)
            if n is None:
                n = {"name": net, "class": "power" if power else "signal", "pins": []}
                nets.append(n)
                by_name[net] = n
            elif power:
                n["class"] = "power"
            n["pins"] += [p for p in pins if p not in n["pins"]]

        unconnected: dict[str, list[str]] = {}
        mapped: dict[str, str] = {}
        for port, pdef in ports.items():
            pins = pads(list(pdef.get("pins") or []) + list((b.get("nets") or {}).get(port) or []))
            if not pins:
                continue
            target = connect.get(port) or pdef.get("default")
            if not target:
                if pdef.get("required"):
                    errors.append(f"block {name} ({bid}): port {port} must be connected ({pdef.get('desc', '')})")
                for p in pins:
                    unconnected.setdefault(p.split(".")[0], []).append(p.split(".", 1)[1])
                continue
            mapped[port] = target
            add_net(target, pins, bool(pdef.get("power")))
        for net, pins in (b.get("nets") or {}).items():
            if net in ports:
                continue
            got = pads(pins)
            if got:
                add_net(f"{name}_{net}", got, False)
        for ref, pads_nc in (b.get("no_connect") or {}).items():
            if ref in local:
                unconnected.setdefault(ref, []).extend(pads_nc)
        placement = entry.get("placement") or {}
        for ref, c in local.items():
            c.pop("option", None)
            c["ref"] = rename[ref]
            c["block"] = name
            c["block_ref"] = ref
            nc = list(dict.fromkeys([str(x) for x in c.get("no_connect") or []] + unconnected.get(ref, [])))
            if nc:
                c["no_connect"] = nc
            if isinstance(placement.get(ref), dict):
                c["placement"] = placement[ref]
            comps.append(c)
        for ref in placement:
            if ref not in local:
                errors.append(f"block {name}: placement for {ref}, which the block does not have")
        # what the block knows about its supply and its controlled-impedance nets
        for s in (b.get("sim") or {}).get("sources") or []:
            net = mapped.get(s["net"])
            if net and not any(x["net"] == net for x in block_sources):
                block_sources.append({**s, "net": net})
        for imp in b.get("impedance") or []:
            nets_i = [mapped.get(n) for n in imp["nets"]]
            if all(nets_i) and not any(set(nets_i) == set(x.get("nets") or []) for x in impedance):
                impedance.append({**imp, "nets": nets_i})
        record.append({"block": bid, "name": name, "connect": mapped, "options": opts,
                       "parts": {ref: rename[ref] for ref in local}})

    spec["components"] = comps
    spec["nets"] = nets
    if block_sources and not sim.get("sources"):
        sim["sources"] = block_sources
    if sim:
        spec["simulation"] = sim
    if impedance:
        spec["impedance"] = impedance
    spec["blocks_used"] = record
    if errors:
        spec["block_errors"] = errors
    return spec


def doc() -> str:
    return __doc__.split("\n\n", 1)[1].strip()
