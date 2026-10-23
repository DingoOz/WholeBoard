# WholeBoard roadmap

Proposed features, ranked by how much each would improve the boards WholeBoard produces. Each item
has a short design note and a rough size: **S** (a day or two), **M** (about a week), **L** (several
weeks).

## Priorities

### 1. Keep hand edits made in KiCad (L)

At the moment, any change a user makes in KiCad after TraceMaker has routed the board is lost on the
next AI rebuild. Reading the edited board back in would let people mix AI work with manual work.

- Before a rebuild, compare the project's `.kicad_pcb` with the board from the last build. If it
  differs, read part positions, rotations and sides back into the spec as `placement` entries with
  `locked: true` (the field already exists).
- Pass hand-routed tracks to TraceMaker as fixed copper, or keep them and route only what is still
  unconnected.
- Report what was imported in the chat ("Kept 12 hand-placed parts and 3 routed nets from KiCad").
- Revision history marks such a revision as "edited in KiCad".

### 2. Readable schematics (M)

The grid of symbols joined by net labels is the first thing a professional engineer will judge.

- Use building blocks as the structure: one sheet per block in a hierarchical schematic, with
  hierarchical labels for the block's ports.
- Parts that are not in a block go on a "main" sheet, grouped by the nets they share.
- Within a sheet, place the parts in signal order: connector, protection, regulator, load.
- Keep the generated sheets passing ERC. Continue to filter out findings that only come from how
  WholeBoard draws schematics.

### 3. A JLCPCB design-rules file (S)

No `.kicad_dru` file is generated at the moment, so KiCad's DRC only checks the spec's basic rules.

- Write a `.kicad_dru` with JLCPCB's capabilities for the chosen layer count and thickness: minimum
  annular ring, hole-to-hole spacing, hole-to-track clearance, silkscreen-to-pad clearance, minimum
  silkscreen text size and copper-to-board-edge clearance.
- Flag via-in-pad and small vias as cost items in the quote, not just DRC errors.
- Keep the values in a JSON table next to `pricing.json` so they are easy to update.

## Design capability

### Parts on both sides (M)

The README lists single-sided SMT as a limit, and JLCPCB assembles both sides.

- Allow `side: "bottom"` for any part, with mirrored footprints and a bottom-side CPL file.
- Add bottom assembly to the quote (JLCPCB charges a second setup).
- Extend the assembly page, renders and enclosure part heights to cover the bottom side.

### More building blocks (M, ongoing)

- Power: a synchronous buck converter (for example TPS563201), a USB-PD sink with a fixed 9 V or
  12 V output (for example CH224K), reverse-polarity protection.
- Microcontrollers: STM32G0 or STM32C0, nRF52840 module, ESP32-C6.
- Interfaces: Ethernet PHY with magnetics, motor driver, common I2C sensors.
- Every microcontroller module with an antenna carries a keep-out area, so the router and the
  ground pour stay clear of it.

### Interface profiles inside blocks (S to M)

Blocks carry the routing limits of the interfaces they provide, so the impedance and length checks
apply wherever the block is used.

- USB-C data block: USB 2.0 high-speed impedance, pair skew and stub limits.
- ESP32-S3 block: PSRAM and flash routing limits, where the module exposes them.
- Profile fields: target impedance, maximum length, maximum skew within a pair and between pairs,
  maximum vias.
- The post-routing impedance check reads the profile instead of a fixed rule.

### Mains safety (M)

WholeBoard should not design a mains-powered board without these checks.

- A net class `hv` with a working voltage.
- Creepage and clearance checks based on IPC-2221 and IEC 62368-1, including slots under
  optocouplers.
- Refuse to finish a design that has mains nets but no fuse, no isolation barrier or a failed
  check, and say why.

### IPC-2152 trace heating (S)

IPC-2152 (2009) replaces IPC-2221's current-carrying charts with measured data. It is less
pessimistic for internal layers. Keep IPC-2221 as an option for comparison.

### Antenna placement with the enclosure and other parts (M for the checks, L for simulation)

Modules with an antenna (ESP32, nRF52840 and the like) already carry a copper keep-out, but nothing
checks what is around the antenna once the board is placed and boxed. A tall connector, a battery,
a screw boss or a metal case next to the antenna can cut the range badly, and the board still passes
every other check.

There is no single official rule for antenna keep-outs. Antenna and chip vendors publish their own
guidance in application notes and module datasheets: a copper-free zone around the antenna, and a
clearance of a few millimetres from plastic or metal. So the plan is to start from the vendor's
numbers for the exact part, and then measure the match with a VNA inside the real enclosure. Rules
and simulation pick the starting layout. The measurement is the final word.

Rule-based checks first, using each vendor's own numbers:

- Give each antenna-bearing footprint (module antennas, chip antennas, u.FL and SMA connectors) an
  antenna region, the vendor's keep-out distances and the direction it should face, taken from the
  module's datasheet, hardware design guide or the antenna maker's application note. Record the
  source document and section with the numbers so they can be checked.
- Keep separate clearances for plastic and for metal, because metal needs far more room. Plastic
  close to the antenna lowers its resonant frequency. Metal close to it detunes the antenna and
  blocks the signal.
- Where the vendor publishes no figures, use a conservative generic keep-out. Label the result
  "generic, not from the datasheet" so nobody mistakes it for the vendor's rule.
- On the board: the antenna end at a board edge or overhanging it, no copper or pour under it, no
  tall or metal parts (connectors, electrolytics, batteries, shields, displays) within the keep-out
  or in front of the antenna, and enough ground plane on the module's side.
- In the enclosure: the distance from the antenna to each wall, lid and floor; no screw bosses,
  standoffs, heat-set inserts or metal fittings near it; a warning for a metal case (or an
  aluminium case from *Fit to a case*) with the choice of a plastic window or an external antenna
  on a u.FL-to-SMA bulkhead, which the enclosure generator can then cut a hole for.
- Feed the rules back into the design: the designer and TraceMaker place the module with its
  antenna outwards, and the enclosure generator keeps walls and bosses clear.
- Show the keep-out as a volume in the board view and the 3D enclosure view, with each problem
  marked where it is.

Then measure it on the real board (bring-up guide):

- Add a step to the bring-up guide for boards with an antenna: measure the return loss (S11) with a
  VNA (a NanoVNA is enough) with the board fitted in its printed enclosure, lid closed, and again
  outside it. Connect through the u.FL connector, or through a short pigtail soldered at the
  antenna feed. Give the band to check (for example 2.400–2.4835 GHz) and the target, about −10 dB
  or better across the band.
- For chip antennas and antennas on a u.FL connector, the designer adds a pi matching network
  (a 0 Ω series part and two unfitted shunt pads) so the antenna can be re-tuned for the enclosure.
  Module antennas usually cannot be re-tuned. For those, a failed measurement means moving the
  module, adding a plastic window or using an external antenna, and the guide says so.
- Let the user enter the measured resonance and S11 so the report keeps them with the revision.

Then simulation, on demand from the Sim Lab:

- Model the antenna, the board's ground plane, nearby parts as metal blocks and the enclosure walls
  as plastic (PLA, PETG, ABS) or metal, and solve it with openEMS (FDTD, GPL-3.0).
- Report the shift in resonant frequency and the return loss (S11) with and without the enclosure,
  and show the radiation pattern on the 3D model.
- Module antennas are proprietary shapes, so use the maker's reference geometry where published and
  a generic inverted-F or chip-antenna model otherwise. Present the results as a comparison between
  layouts, not a prediction of range and not a replacement for the VNA measurement or for
  certification testing.

## Manufacturing

### Panel files (M)

The quote prices a panel but does not make one.

- Generate the panel with KiKit (MIT licence): V-cut or tab routing, 5 mm rails, mouse bites.
- Add fiducials and tooling holes on the rails. Fiducials are not added anywhere at the moment;
  consider them on single boards too, for parts with fine pitch.
- Produce panel Gerbers and a panel CPL, and show the panel in the board view.

### More suppliers (M)

- Mouser and DigiKey prices for parts fitted by hand or not stocked at JLCPCB.
- Assembly quotes from PCBWay and Aisler, for people JLCPCB does not suit.

## Integration

### An MCP server (M)

Expose WholeBoard's checks as tools any agent can call, so Claude Code, Codex or Backplane agents
can use them on boards WholeBoard did not design.

- Tools: `simulate`, `power_budget`, `impedance_check`, `board_checks`, `jlc_quote`,
  `stock_check`, `bringup_guide`.
- Inputs: a KiCad project path, reusing the existing KiCad importer to build a spec.
- Run as `python -m wholeboard.mcp` over stdio.

### Altium (S)

- A README section explaining that Altium Designer can open WholeBoard's KiCad project with its
  KiCad Importer extension.
- Altium to WholeBoard: document importing the Altium project in KiCad first, then using
  *Import KiCad project*.

## Quality

### A benchmark set (M)

Measure whether a change to a model, a prompt or a block helps or hurts.

- 10 to 20 reference prompts, from a 555 blinker to an ESP32-S3 sensor board, a CAN node and a
  LiPo-powered board.
- For each run, record: completed or not, AI rebuilds needed, ERC and DRC findings, unrouted
  connections, simulation failures, board cost, tokens and API cost.
- Run on a schedule and on demand, with results in a table in the repository. Use them to
  recommend a worker and expert model pairing in the README.
