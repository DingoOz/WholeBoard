# Place an antenna and check it

A Wi-Fi or Bluetooth module can pass every other check and still have half its range cut off by a USB connector,
a battery or the box's screw next to its antenna. WholeBoard checks what is around each antenna on the board and in
the enclosure, adds a VNA step to the bring-up guide and can simulate the antenna in its box.

There is no single official rule for antenna keep-outs, so each check uses the antenna vendor's own numbers and
says which document they come from. Where the vendor publishes none, WholeBoard uses a conservative generic figure
and labels it *generic, not from the datasheet*. Rules pick the starting layout; a measurement in the real
enclosure is the final word.

## Which antennas are checked

| Antenna | Recognised by | Numbers from |
|---|---|---|
| Espressif modules (ESP32, ESP32-S3, ESP32-C3, ESP32-C6, …) | `RF_Module:ESP32*` footprints | Espressif's hardware design guidelines: antenna outside the board or its feed at the edge; 15 mm clearance in all directions inside the housing. The 15 mm board keep-out is drawn in KiCad's footprint |
| Raytac nRF52840 modules (MDBT50Q) | `RF_Module:Raytac_MDBT*` | Raytac's specification §2.3: module towards the board edge, no ground under the antenna on any layer. Raytac gives no distances, so the housing figures are generic |
| Chip antennas | `RF_Antenna:*` footprints | Generic figures; follow the antenna maker's evaluation-board layout |
| Antenna connectors (u.FL, SMA) | `Connector_Coaxial:U.FL*`, `SMA*` | Only the matching-network check applies |
| Any other part | `"antenna": true` on the component | Generic figures |

The antenna area is where the footprint's keep-out zone overlaps the module's body, and it faces away from the
rest of the module.

## Place the module

Fix the module's placement at a board edge, turned so its antenna end points out of the board. KiCad's
`RF_Module` footprints have the antenna at the top, so use rotation 0 for the board's top edge, 90 for the left,
180 for the bottom and 270 for the right:

```json
{"ref": "U1", "footprint": "RF_Module:ESP32-C3-WROOM-02",
 "placement": {"x_mm": 30, "y_mm": 13, "rotation": 0, "locked": true}}
```

The design check warns, before anything is drawn, about a module with no placement or with its antenna away from
the edge. The designer follows the same rule.

## What the check reports

After the DRC, the **Antenna placement** card shows each antenna on a plan of the board around it, with its
keep-out dashed and every finding marked:

- **Edge**: how far the antenna's end is inside the board edge (negative when it overhangs). More than 1 mm inside
  is a warning for a module and a problem for a chip antenna.
- **Copper under the antenna**: tracks, vias, pads or pours on any layer. A problem.
- **Parts**: a part inside the vendor's keep-out is a problem. A tall or metal part (connector, battery,
  electrolytic, display, shield, inductor, crystal can) in front of the antenna within its metal clearance, or
  beside it within 5 mm, is a warning.
- **Ground plane**: less than half of the board under the rest of the module covered by ground copper is a
  warning.
- **Matching network**: a chip antenna or antenna connector with no pi network on its feed (see below).

*Where the numbers come from* under each antenna lists the document behind every figure.

## In the enclosure

When the board has an enclosure, the card adds the distance from each antenna to every wall, the lid and the floor,
and to the metal fittings: board screws, lid screws and heat-set inserts. Plastic gets the vendor's housing
clearance; metal gets the (larger) metal clearance.

- The printed enclosure moves the wall the antenna faces outwards to give it the vendor's clearance, up to 15 mm.
  Set `"antenna_room": false` in the enclosure design to keep the box tight. The 3D preview shows each antenna's
  keep-out as a see-through orange volume (*Antenna keep-out* toggles it).
- A stock case of aluminium or another metal is a problem: cut a plastic window over the antenna, or use an
  external antenna on a u.FL connector with a u.FL-to-SMA bulkhead through the wall.

## Add a matching network

A chip antenna or an antenna on a u.FL connector should have a pi network between the radio and the antenna, so it
can be re-tuned for the enclosure after measuring. Add the `antenna_pi_match` block:

```json
{"block": "antenna_pi_match", "name": "M1", "connect": {"RF_IN": "RF_OUT", "RF_ANT": "ANT"}}
```

It is a 0 Ω series part (R1) and two unfitted shunt pads (C1, C2), in 0402, with 50 Ω feed entries for the
impedance check. Module antennas are matched inside the module and cannot be re-tuned.

## Measure it

Boards with an antenna get an **Antenna match (VNA)** step in the bring-up guide: calibrate a VNA (a NanoVNA is
enough), sweep the return loss (S11) across the band, in the closed enclosure and outside it, and aim for −10 dB or
better across the band. The step says how to connect for each kind of antenna and what to do when it fails.

Enter the results under the bring-up guide (antenna, in or out of the box, resonance, best S11, worst S11 in the
band, a note). They are kept with the board's revision and printed in the project report.

## Simulate it

The Simulation step has an **Antenna simulation (openEMS)** card. *Simulate the antenna* models the antenna, the
board's ground plane, nearby metal parts and the enclosure (plastic walls with typical permittivity, or metal), and
solves it with [openEMS](https://www.openems.de) twice: on the bare board and in the box. It reports the resonance,
the best and worst S11 in the band and the −10 dB bandwidth of each, plots both S11 curves and draws the radiation
pattern in the box.

Module antennas are proprietary shapes, so a generic inverted-F about a quarter wave long stands in for them. Read
the result as a comparison (how much the box moves the resonance), not as a prediction of range, and measure the
real board.

openEMS is optional. Install it with:

```
.venv/bin/python -m wholeboard.doctor --install openems
```

This uses an existing Python with openEMS if there is one; otherwise it builds a small Docker image from Debian's
`python3-openems` package. Each of the two solves takes about ten minutes on a desktop computer.

## From another agent

The MCP tool `antenna_check` runs the same board checks on any KiCad project, and with a `case` the enclosure
distances too. `bringup_guide` includes the VNA step for boards with an antenna.
