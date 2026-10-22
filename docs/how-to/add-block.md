# Add your own building block

A building block is a tested sub-circuit the designer drops into a design whole. To add your own, save it as a
JSON file in `~/.wholeboard/blocks/` (one block per file). It appears in the designer's `list_blocks` straight
away, next to the [built-in blocks](../reference/blocks.md); a file with the same `id` as a built-in block
replaces it.

## An example

The built-in indicator LED, as a file:

```json
{
  "id": "led_indicator",
  "category": "indicator",
  "title": "Indicator LED with series resistor (red 0603, 1 kΩ)",
  "description": "LED from IN to GND through 1 kΩ: about 1.3 mA from a 3.3 V pin, 3 mA from 5 V.",
  "components": [
    {"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0603_1608Metric", "symbol": "Device:R",
     "lcsc": "C21190", "description": "LED series resistor"},
    {"ref": "D1", "value": "Red", "footprint": "LED_SMD:LED_0603_1608Metric", "symbol": "Device:LED",
     "lcsc": "C2286", "description": "Indicator LED"}
  ],
  "ports": {
    "IN":  {"desc": "Drive high to light (an MCU pin or a supply rail)", "required": true, "pins": ["R1.1"]},
    "GND": {"desc": "Ground", "default": "GND", "power": true, "pins": ["D1.1"]}
  },
  "nets": {"LED_A": ["R1.2", "D1.2"]}
}
```

## Fields

| Field | What it is |
|---|---|
| `id` | Unique name, `snake_case` |
| `category` | Groups blocks in the list: `power input`, `regulator`, `microcontroller`, `interface`, … |
| `title` | One line the designer sees in the list |
| `description` | What it does and how to place it. The designer reads this, so say what matters: which edge, which way round, what to change for other values |
| `components` | Parts, in the [design spec's](../reference/spec.md) component format, with block-local references (`R1`, `U1`). Give `"option": "<name>"` to make a part optional |
| `ports` | What the design connects to. Each has `desc`; `pins` inside the block; `default` (a net name used when the design does not connect it); `required`; `power` (a supply or ground) |
| `nets` | The block's inner nets. They become `<name>_<net>` in the design |
| `no_connect` | `{"U1": ["5", "6"]}`: pads deliberately left open |
| `options` | `{"esd": {"default": true, "desc": "…"}}`: switches the design can set, matched by the parts' `option` |
| `sim` | Supplies and expectations for the simulation, as in the spec's `simulation` section |
| `impedance` | Controlled nets, with an [interface profile](../reference/profiles.md): `[{"nets": ["DP", "DN"], "kind": "diff", "profile": "usb2_hs"}]` |

Pins are `REF.PAD` (a footprint pad) or `REF:PIN_NAME` (every pad of that symbol pin, such as `J1:GND`).

## Check it

Ask the designer to use the block in a small board. The design check reports pads that do not exist and pins
that are not connected; ERC and the simulation do the rest. A block is worth adding once a board that uses it has
built cleanly.
