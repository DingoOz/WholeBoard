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
