# WholeBoard

> **🚧 Under construction.** WholeBoard is in active development. Expect rough edges, breaking
> changes and boards that need a human check before you order them.

An AI workbench for printed circuit boards. Describe the board you want in plain words.
WholeBoard's designer agent asks you what it needs to know, picks real JLCPCB parts and KiCad
footprints, writes the schematic and board, has **TraceMaker** place and route it, checks it with
**KiCad's** design rule check, and prices the whole job at **JLCPCB**: bare PCB, assembly, every
component and shipping. It then designs a 3D-printable enclosure for the board in **FreeCAD**.
Each step shows a visual you can inspect.

Runs on Linux, macOS and Windows. It is a local web app: the server runs on your machine and the
interface opens in your browser.

![A finished board in WholeBoard: the designer's chat on the left, the routed board and KiCad DRC results on the right](docs/screenshots/board.png)

![The simulation step: ngspice results for a 555 blinker, with rail and timing voltages plotted over time](docs/screenshots/simulation.png)

![The enclosure step: a 3D preview of the FreeCAD-built box with its engraved lid lifted off the board](docs/screenshots/enclosure.png)

## What each step shows

New to WholeBoard? Press **Take a tour** in the top bar: it walks through a normal workflow, from
describing a board to the quote and enclosure, highlighting each part of the page in turn (on one
of your own boards when you have one).

| Step | What happens | Visual |
|---|---|---|
| Requirements | The AI reads your request and asks questions in the chat | Conversation |
| Parts & architecture | Starts from proven building blocks, searches JLCPCB parts, KiCad footprints and symbols | Tool activity in the chat |
| Design check | Footprints exist, pad names are right, nets are sane, parts are in stock | Parts and nets tables |
| Schematic & ERC | A hierarchical KiCad schematic, one sheet per building block, laid out left to right in signal order, checked by KiCad's electrical rules check | Zoomable schematic with a tab per sheet, ERC findings |
| Simulation | ngspice runs the circuit: rail voltages, currents, part ratings, heat, battery life, oscillator frequency, filter response | Plots, ratings table, the SPICE netlist |
| Power budget | Current per supply and rail, regulator headroom and heat, battery life on a duty cycle | Budget meters, thermal table |
| Sim Lab | The simulation on the finished 3D board: current flow, LED brightness, a heat map, every risk with sliders that re-run ngspice | Interactive 3D view, guided tour, recorded video |
| Board & footprints | Outline, mounting holes, footprints with nets | Board view |
| Placement | `tracemaker-place` places the parts, on both sides when the board allows it; isolation slots are cut under mains barrier parts | Board view with air wires |
| Routing | `tracemaker route` routes every connection | TraceMaker's live viewer, then the routed board |
| DRC & copper pour | Ground pour, zone fill, KiCad DRC against JLCPCB's rules file, then trace heating, voltage drop, decoupling-capacitor distances, interface routing limits and mains creepage | Final board, DRC summary, board checks |
| 3D views & fab files | 3D renders with real part models, Gerbers, drill, BOM and CPL (rotations corrected for JLCPCB), an interactive assembly page, a bring-up guide, starter firmware for microcontroller boards | Renders, downloads, pin map, bring-up checklist |
| JLCPCB quote | PCB + assembly + components + shipping, cheaper drop-in parts, panel options and panel files, Mouser and DigiKey for parts JLCPCB cannot fit, OSH Park, PCBWay and Aisler for comparison, a live stock check | Itemised quote you can re-run with other options |
| AI visual review | The AI looks at the renders like a reviewer and fixes real problems | Review notes |
| 3D-printed enclosure | The AI designs a box and FreeCAD builds and checks it, or fits the board to a box you already have | 3D preview, collision report, STL/STEP/FreeCAD downloads, drilling templates |
| Project report | One PDF with the renders, quote, BOM, simulation, power budget, checks, bring-up steps and schematic | Download |

If a step fails (a pad name is wrong, routing leaves connections open, DRC finds errors), the
result goes back to the AI, which fixes the design and builds it again. You can also edit the
design JSON yourself and rebuild without the AI.

The header shows how many tokens the AI has used on the board and what that cost at list prices.
For OpenAI-compatible services, set their price per million tokens in Settings. Local models count
as free.

## Catching mistakes before you order

- **ERC.** KiCad's electrical rules check runs on the generated schematic. Findings that only
  reflect how WholeBoard draws schematics are filtered out.
- **Simulation.** Every board is simulated with ngspice. Resistors, capacitors, LEDs, diodes,
  transistors, regulators, switching converters, 555 timers, op-amps and IC loads get behavioural
  models, and supplies are found from the connectors, batteries and net names. The AI can add
  expectations ("LED_A blinks at 1 Hz", "3V3 within 3 %") and part overrides; failed expectations
  or overloaded parts go back to it before the board is built.
- **Board checks.** Track temperature rise for the simulated currents (IPC-2152, with IPC-2221
  alongside for comparison; `rules.trace_heating` picks which one the check uses), voltage drop
  along power nets, and decoupling capacitors that sit too far from their ICs.
- **JLCPCB's design rules.** Every board gets a KiCad custom-rules file (`.kicad_dru`) with
  JLCPCB's limits for its layer count: annular rings, hole spacing, hole to copper, copper to the
  edge, silkscreen size and clearance, and the drill aspect ratio. KiCad's DRC checks them and
  TraceMaker routes to them. The quote lists features JLCPCB charges extra for (small vias, vias in
  pads) or cannot make. The limits are in `wholeboard/jlc/capabilities.json`.
- **Mains safety.** A design that touches mains declares its mains nets and working voltage in
  `mains`. WholeBoard refuses to finish it without a fuse on the input or, for an isolated design, an
  isolation barrier (an isolated supply module, transformer, optocoupler or relay). The mains nets
  are kept apart by IPC-2221's spacing for the peak voltage, and everything on the mains side is kept
  from the low-voltage side by IEC 62368-1 reinforced clearance and creepage (3 mm and 5 mm at
  230 V). Milled slots are cut under optocouplers whose pins are too close, and the creepage check
  goes around them. KiCad's DRC and TraceMaker get the same rules; any spacing that fails stops the
  build. These checks are an aid, not a certification.
- **CPL rotations.** JLCPCB draws many packages turned from KiCad's zero angle. The CPL file is
  corrected (SOT-23, SOIC, QFN and so on), and a component's `cpl` entry can override it.
- **AI visual review.** After a clean build, the AI is shown the 3D renders and looks for parts
  off the board, blocked connectors and hidden LEDs. Models that cannot see images skip this
  step, and *Settings → AI → Vision* turns it off.

## Building blocks

The designer starts from a library of proven circuits instead of drawing everything from scratch:

- **Power:** USB-C power and data (with optional ESD protection), USB-C Power Delivery triggers for
  9 V and 12 V (CH224K), three 3.3 V regulators, TPS563201 3 A buck converters for 3.3 V and 5 V,
  P-MOSFET reverse-polarity protection and a LiPo charger.
- **Processors:** ESP32-S3, ESP32-C3 and ESP32-C6 modules, nRF52840 (MDBT50Q), RP2040,
  STM32G030 and STM32C011, each with its support parts and a programming connector.
- **Interfaces and parts:** 10/100 Ethernet (LAN8720A with an RJ45 MagJack), CAN, RS-485, a
  USB-serial bridge, a DRV8833 motor driver, BME280 and SHT40 sensors, a Qwiic I²C connector, an
  indicator LED and a button.

Each block has real LCSC parts and named ports; the design connects ports to its own nets, and the
block's parts are renumbered and merged in. Blocks carry options (for example, leaving out the I²C
pull-ups), supply definitions for the simulation, and the routing limits of the interfaces they
provide: the USB block's pair is checked against USB 2.0 high speed, the Ethernet block's pairs and
RMII bus against 100BASE-TX and RMII, the RP2040's flash bus against QSPI. Radio modules keep
copper out from under their antennas. Put your own blocks as JSON files in `~/.wholeboard/blocks/`.

## Two models: a worker and an expert

*Settings → AI → Expert model* adds a second, stronger model. The everyday model does every turn:
part searches, writing the design, fixing build errors. The expert is only called to plan the
architecture once the requirements are clear, to review each clean build before ordering, when the
worker has failed to build three times in a row, and when the worker asks it a question. A cheap
or local worker with Claude Opus as the expert costs much less than running the expert on every
turn. The token count in the header splits usage by model.

## Power, heat and impedance

- **Power budget.** After the simulation, each supply shows its typical and peak current against
  its limit (500 mA for USB, the battery's rating, a bench supply's setting), each regulator its
  load, headroom, dissipation and the thermal resistance it needs to stay cool, and each rail the
  parts that draw from it. Battery boards can give an active/sleep duty cycle for a realistic
  battery life.
- **Controlled impedance.** The design can mark nets as 50 Ω single-ended or 90/100 Ω differential
  pairs. The trace width and pair gap are worked out for JLCPCB's stack-up (JLC04161H-7628 on
  4 layers), written to KiCad net classes, and TraceMaker routes the pairs together. After routing,
  the lengths, skew and vias are checked, and the quote reminds you to order impedance control.
  On 2-layer boards the widths are impractical, so the check says so (USB full speed is fine
  without it).
- **Interface profiles.** An impedance entry can name a profile (`usb2_hs`, `usb2_fs`,
  `ethernet_100`, `rmii`, `qspi_flash`, `rf_50`) for that interface's limits: maximum length,
  skew within a pair and between pairs, length matching across a bus, vias, and stubs (a branch
  off the main run, such as a tee to an ESD diode). The post-routing check reads the profile.

## Sim Lab: the simulation on the 3D board

Once the board is routed, the Simulate step opens the Sim Lab: the finished board in 3D (drag to
turn, wheel to zoom, right-drag to pan) with the ngspice results drawn on it.

- **Current flow.** Dashes run along each track from the supply to the parts and back on ground,
  brighter and faster where more current flows.
- **LED glow.** Each LED lights in its own colour as brightly as its simulated current; RGB LEDs mix
  their channels.
- **Heat map.** Each part's dissipation spreads through the FR4 and copper (pours, tracks, pads)
  and is lost to the air from both faces. The board and every part are coloured by temperature.
  The model is a 2D plate solved in the browser, good for spotting hot spots and comparing
  changes, not a substitute for a thermal camera.
- **Risks.** Simulation problems, board-check findings, power-budget notes, LEDs over their
  current and the hottest parts are listed beside the board. Click one to fly to the parts it
  names and get the sliders that change it.
- **Sliders.** Resistor values, supply voltages, IC load currents and the air temperature re-run
  ngspice in under a second; cooling (still air to a fan) and copper weight change the heat map at
  once. The design itself is not changed; ask the designer in the chat to make a change stick.
- **In its enclosure.** Put the board in its box: the printed enclosure, your stock case, or (before
  one is designed) the box WholeBoard would suggest. Boards with an enclosure start in it. The box is
  drawn see-through and coloured by its surface temperatures. The circuit is re-simulated in what
  the board sees inside: warmer air that barely moves, plus the cooler walls it radiates to. Every
  rating check, part temperature and the heat map then reflect the box. The controls choose:
  - the vents: as designed, sealed, lid slots, side slots, or both
  - the wall material: PLA, PETG, ABS, ASA, PC, bare or painted aluminium
  - how the box stands: on a desk, or on feet or hanging

  The readout gives the inside air, the lid, wall and base temperatures and the air flowing
  through the vents.
- **Tour and video.** Play tour flies through the board: current, LEDs, heat, the board in its box
  with and without vents, each risk and a what-if. Record video saves the same tour as a 1280 × 720 MP4 with the project's files, and
  you can ask the designer in the chat for a video demo.

## Bring-up, assembly and flashing

- **Test pads.** With `board.test_points`, every power rail and ground gets a test pad. They are
  not part of the BOM.
- **Bring-up guide.** A step-by-step checklist for the first board: what to look at, which nets to
  check for shorts, a current-limited power-up with the expected current, rail voltages with
  tolerances at their test pads, parts to feel for heat, LEDs and signals to see, how to program it
  and which interfaces to test. It is in the outputs and in `exports/`.
- **Assembly page.** A self-contained HTML page with the board from both sides and the BOM grouped
  by who fits each part (JLCPCB, by hand, not fitted). Click a line to highlight its parts, and tick
  them off as you go.
- **Build and flash.** *Compile with PlatformIO* on the firmware card compiles the starter firmware
  (it offers to install PlatformIO the first time; `python -m wholeboard.doctor --install platformio`
  also installs it).
  ESP32 boards can then be flashed from the browser over Web Serial (Chrome or Edge, on
  `localhost` or HTTPS). RP2040 boards get a `.uf2` file to drop on the BOOTSEL drive.

## Parts on both sides, and panels

- **Both sides.** With `board.double_sided`, TraceMaker's placer may put parts on the bottom, and a
  part's `placement.side` pins it to one side. Bottom-side parts are flipped exactly as KiCad flips
  them, and the quote adds JLCPCB's second-side setup and stencil (standard assembly only).
- **Panels.** *Make the panel files* in the quote (or `board.panel` in the design) draws the panel
  with KiKit: V-cut for square corners, tabs with mouse bites for rounded ones, 5 mm rails with
  three tooling holes and three fiducials. You get panel Gerbers, a panel BOM and CPL with each
  copy's parts renamed (`R1_0`, `R1_1` …), and the panel in the board view. KiKit runs on KiCad's
  Python: `python -m wholeboard.doctor --install kikit`.
- **Fiducials on single boards.** Parts at 0.5 mm pitch or finer need fiducials for the assembly
  machine. `board.fiducials` puts three in the corners; WholeBoard warns when they are missing.

## Fitting a board to a case

- **Board outline from a DXF.** *Board outline (DXF)* reads a drawing from your CAD program:
  lines, arcs, circles and polylines, in millimetres or inches. The largest closed shape becomes
  the board edge, and the circles inside it become mounting holes. The designer then builds the
  board to that shape.
- **A box you already have.** Instead of designing an enclosure, the design can name an existing
  box with its inside size, wall, floor and standoff height. WholeBoard checks the board and its
  tallest parts fit, then draws 1:1 drilling templates (SVG, with a 50 mm scale bar) for every
  wall with a connector and for lid holes over LEDs and buttons.

## Starting from an existing KiCad project

*Import KiCad project…* on the home page takes a `.kicad_sch` (and its `.kicad_pcb`), or a zip
of the project folder. KiCad reads the schematic's netlist: parts, values, footprints, LCSC numbers
(from an `LCSC`, `LCSC Part` or `JLCPCB Part` field) and nets. The board, if given, supplies the
outline, layer count, mounting holes and part positions. The AI then checks the design, finds
JLCPCB parts for anything without an LCSC number, swaps footprints that are not in KiCad's
standard libraries, and builds it. With no AI configured, the import is built as it is.

## Editing the board in KiCad

Open the board from the outputs folder in KiCad, move parts, reroute or widen tracks, and save.
The next build notices: moved parts keep their positions, edited tracks and vias are kept as
locked copper, and TraceMaker routes only what is left. The chat says what was kept, and *History*
marks the revision as edited in KiCad. Set `"keep_kicad_edits": false` in the design to start from
scratch again.

## Altium Designer

- **WholeBoard to Altium.** WholeBoard's output is an ordinary KiCad project (in `exports/` as a zip,
  or the `revN` folder). Altium Designer opens it with its KiCad importer: *File → Import Wizard →
  KiCad Design Files*, then choose the `.kicad_pro`. The importer is an Altium extension; install it
  from *Extensions and Updates* if the wizard does not list KiCad. Schematic, board, net classes
  and rules come across; check the design rules and polygon pours after the import, since the two
  tools treat them differently.
- **Altium to WholeBoard.** Import the Altium project into KiCad first (KiCad's *File → Import →
  Non-KiCad Project…* reads `.PrjPcb`, `.SchDoc` and `.PcbDoc`), save it as a KiCad project, then use
  *Import KiCad project…* in WholeBoard. Footprints from Altium libraries arrive as project-specific
  footprints, which WholeBoard asks to replace with standard KiCad ones.

## Using WholeBoard's checks from other agents (MCP)

`python -m wholeboard.mcp` is a Model Context Protocol server over stdio. It gives any agent
(Claude Code, Codex and others) WholeBoard's checks on any KiCad project, designed by WholeBoard or
not:

| Tool | What it does |
|---|---|
| `simulate` | ngspice simulation: supply currents, rail voltages, oscillator frequencies, rating problems |
| `power_budget` | Load and heat for every supply and regulator, battery life |
| `impedance_check` | Pair widths, skew, length, vias and stubs against interface profiles |
| `board_checks` | Trace heating, voltage drop, decoupling distance, and mains creepage when told the mains nets |
| `jlc_quote` | A JLCPCB quote with live part prices |
| `stock_check` | Live JLCPCB stock, with replacements for short parts |
| `bringup_guide` | A bring-up checklist in Markdown |

Each tool takes `project`: a KiCad project folder or a file in it. To add it to Claude Code, run
`claude mcp add wholeboard -- /path/to/WholeBoard/.venv/bin/python -m wholeboard.mcp`.

## Revision history

Every build is kept in its own `revN` folder. *History* lists them with a render, the board size,
the price and what changed from the build before. Tick two to compare parts, values, footprints,
LCSC numbers, nets, board settings and rules. *Restore* rebuilds an old design as a new revision.
Nothing is deleted.

## Starter firmware

When the board has an ESP32 (any variant), RP2040/RP2350, STM32, ATmega328P/32U4 or ATtiny85,
the outputs include a PlatformIO project (Arduino framework). `include/pins.h` names every GPIO
after its net, using the pin names in the KiCad symbol: `#define PIN_LED_STATUS 4`. The starter
`main.cpp` blinks the LEDs, reads buttons and analog inputs, and scans the I²C bus. Build it with
`pio run -t upload`.

## Install

The installer sets up everything: Python, KiCad, TraceMaker, FreeCAD, KiCad's libraries and
WholeBoard's Python packages. It asks before installing system packages.

Linux (Ubuntu, Debian, Fedora, Arch, openSUSE) and macOS:

```
./install.sh
```

Windows 10 or 11: double-click `install.bat`. It uses winget for Python, Git, KiCad and FreeCAD,
and installs TraceMaker inside WSL, because TraceMaker runs on Linux. If WSL is not set up, the
installer offers to set it up. Restart Windows afterwards and run `install.bat` again.

| What | Linux | macOS | Windows |
|---|---|---|---|
| Python 3.10+, git | package manager | Homebrew | winget |
| KiCad 10 | KiCad's PPA on Ubuntu, the distribution's package elsewhere, Flathub if that is too old | Homebrew | winget |
| TraceMaker | ready-made release (x86_64, glibc 2.38+), otherwise built from source | built from source | release inside WSL |
| FreeCAD | official AppImage in `~/.wholeboard/tools` | Homebrew | winget |
| KiCad libraries | with KiCad, or downloaded | with KiCad | with KiCad |

To check what is installed at any time, or to install a missing tool on its own, run:

```
.venv/bin/python -m wholeboard.doctor
.venv/bin/python -m wholeboard.doctor --install freecad
```

On Windows, use `.venv\Scripts\python` instead.

## The enclosure

Once the board is clean and quoted, the designer plans an enclosure and FreeCAD builds it.

1. The AI reads the finished board: its size, its mounting holes, and every part with position,
   estimated height and nearest edge.
2. It decides what the box needs. It asks you about real choices, such as a screwed or press-fit
   lid, wall mounting or a label. Then it writes an enclosure design, with openings for connectors,
   lid holes for LEDs and buttons, how the board is held, vents, rubber-foot pockets, mounting
   ears and an engraved label.
3. FreeCAD builds the base and lid, then checks every part against them. Collisions go back to the
   AI, which fixes the design and builds again.

The step shows a 3D preview and the details. You can download `base.stl` and `lid.stl` for
printing, STEP files, the FreeCAD model, and a FreeCAD macro that rebuilds the parts from editable
numbers. To change the box, ask the designer in the chat, or use *Edit enclosure JSON*. If you
change the board, the enclosure is rebuilt around it.

Part heights come from the 3D models. KiCad's model for the footprint is used first, then the
model on the part's LCSC/EasyEDA page. Footprint names are the fallback, and the AI can give
datasheet heights. Lids can be a press-fit lip, four screws, or **snap-fit**: a deeper skirt with
bumps that click into grooves in the walls, plus a notch in the front wall to lever it off.

**Heat in the box.** The step also reports, from the board's simulated heat, how warm the air in
the box gets, how hot the lid and walls are to the touch, and how hot the hottest parts run in the
box. It also says what lid and side vents would change. If the box runs warm, the designer is told
and adds vents or headroom. A stock case is checked the same way; give its `material` (ABS, PC,
aluminium) in `board.case`.

The model (`wholeboard/boxheat.py`) treats the box as a small network of heat paths:
- from the board to the inside air, and by radiation to each wall
- from the inside air to the walls
- through the walls by conduction, and round the box from one face to the next
- from each outside face to the room, with a base on a desk losing little
- through the vents and the gaps round connectors, which draw air like a chimney

It is good to a few degrees for small boxes in still air. It is not CFD.

The details include filament, print time and cost, worked out from the material, filament price,
printer speed and electricity price in *Settings → JLCPCB → 3D printing*. Print the base open side
up and the lid flat side down. Neither needs supports. Check the openings against your parts'
datasheets before printing.

## Requirements

- **Python 3.10+**
- **KiCad 9 or 10** (`kicad-cli` is used for DRC, zone fill, renders and fab outputs).
  Found automatically in the usual install folders, or set the path in Settings.
- **TraceMaker** (https://github.com/DingoOz/TraceMaker) for placement and routing. It runs on
  Linux and macOS. On Windows it runs inside WSL ("Run TraceMaker inside WSL" in Settings).
- **FreeCAD 1.0 or newer** for enclosures. Found automatically, or set its path in Settings.
- **ngspice** for simulation: the `ngspice` program, KiCad's own `libngspice`, or the KiCad Docker
  image. The installers add it, and *Settings → Install ngspice* fetches it on Windows and macOS.
- **PlatformIO** (optional) to compile the starter firmware. WholeBoard and the doctor can install it.
- **3D part models** are taken from your KiCad install. Any missing model is downloaded once from
  KiCad's library into `~/.wholeboard/models3d` (*Settings → KiCad & TraceMaker → 3D part models*).
- **KiCad footprint and symbol libraries.** Normally installed with KiCad. If they are missing
  (for example when `kicad-cli` runs from Docker), use *Settings → Download KiCad libraries*.
- **An AI**, either:
  - **Claude** through the Anthropic API (set a key in Settings or `ANTHROPIC_API_KEY`), default
    model `claude-opus-5-5`; or
  - **OpenAI or Grok (xAI)** with an API key; or
  - **any OpenAI-compatible server**: local models through Ollama (`http://localhost:11434/v1`),
    LM Studio, llama.cpp server or vLLM. The model must support tool calling.
    Bigger models design better boards.

  *Settings → How to get an API key* walks through getting a key from Anthropic, OpenAI or xAI.
  Quick-setup buttons fill in the server address, and *List models* shows the models your key can
  use. You can also set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `XAI_API_KEY` before starting.

## Start

Linux / macOS:

```
./run.sh
```

Windows:

```
run.bat
```

After `install.sh` you can also type `wholeboard`. On Windows, use the Start menu entry. If you
skipped the installer, the first run creates a virtual environment and installs the Python
packages, but KiCad, TraceMaker and FreeCAD must already be installed. The browser opens at
http://127.0.0.1:8770/. Options: `--port 8800`, `--no-browser`, `--host 0.0.0.0` (to use it from
other computers), `--set-password` and `--clear-password`.

Or install it as a command: `pip install -e .` then `wholeboard`.


### Using it from another computer

Start it with `./run.sh --host 0.0.0.0` (or `run.bat --host 0.0.0.0`) and open `http://<this machine's IP>:8770/` on the other computer.
WholeBoard prints the address at startup. The TraceMaker live view then listens on the network too, protected by a random token.
Set a password so that nobody else on the network can use WholeBoard or your AI key: run
`wholeboard --set-password`, or use *Settings → Network* on the computer running it. Other
computers then log in once; the computer running WholeBoard never needs to. Only a hash of the
password is stored, and changing it signs everyone out. Without a password, use it on a network
you trust.

## The JLCPCB quote

- **Component prices, stock, and basic/preferred/extended status are live** from JLCPCB's parts
  library.
- **PCB fabrication, assembly fees and shipping are estimates** from an editable price table,
  `wholeboard/jlc/pricing.json`. JLCPCB does not publish a pricing formula, and its official API
  (api.jlcpcb.com) needs an approved developer account. Update the table when JLCPCB changes its
  prices, and confirm the final price on cart.jlcpcb.com with the generated Gerber zip, BOM and CPL.
- **Cheaper parts.** Extended parts (a $3 feeder fee each) and parts that are short of stock are
  matched against drop-in replacements: the same resistor, capacitor or LED value in the same
  package (with an equal or better voltage rating and tolerance), or the same part number under a
  basic/preferred LCSC number. Tick the swaps in the quote to apply them. The BOM and CPL are
  rewritten; the board does not change.
- **Panels.** For small boards, or when it is cheaper, the quote suggests a panel (columns × rows,
  V-cut or tab routing, 5 mm rails) and compares the price. Choose a panel in the quote options to
  price it, and *Make the panel files* to draw it with KiKit (see above). Without panel files,
  JLCPCB builds it from the single-board files when you pick *Panel by JLCPCB*.
- **Other fabs and assemblers.** OSH Park's bare-board price is worked out from their published
  price per square inch (sets of three, shipping included). Aisler's boards, stencil and assembly
  are worked out from their published formulas (in euros, converted). PCBWay is shown with their
  promotional small-board price, marked as indicative, and a link to their assembly quote, which an
  engineer prices by hand.
- **Mouser and DigiKey.** Parts JLCPCB cannot fit (no LCSC number, not found, short of stock) are
  priced at Mouser and DigiKey by manufacturer part number, with stock and the best offer. Add a
  Mouser Search API key or a DigiKey API app (client ID and secret) in *Settings → JLCPCB*; without
  them the quote links to their search pages.
- **Live stock check.** *Check stock* asks JLCPCB again for every part just before you order.
  Parts that are short, low, discontinued or not found are flagged, with drop-in replacements.
- **Project report.** A PDF of the whole board for a design review or your records, rebuilt after
  each quote and enclosure.
- The CPL is already corrected for JLCPCB's part orientations, but still check the assembly preview
  before ordering.

## Where things are saved

- Projects: `~/WholeBoardProjects/<date>-<name>/`, one `revN` folder per build with the KiCad
  project (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`), intermediate boards, renders, `fab/`
  (Gerber zip, BOM, CPL), `sim/` (the SPICE netlist and plots), `firmware/`, `quote.json` and
  `summary.json` (the history entry). `exports/` holds the zipped KiCad project and firmware.
- Settings and API keys: `~/.wholeboard/config.json` (readable by your user only on Linux/macOS).
- Library copies, downloaded 3D models and the parts cache: `~/.wholeboard/`.

## Limits

- 1, 2, 4 or 6 copper layers. Bottom-side assembly needs JLCPCB's standard assembly.
- The schematic is generated from the netlist: correct and usable in KiCad, with a sheet per block
  and parts grouped by signal flow, but joined by net labels rather than wires drawn by hand.
- The mains checks cover spacing on the board, the fuse and the barrier. They are not a safety
  certification: have a mains design reviewed.
- Autorouting quality depends on TraceMaker; dense BGA designs may need manual work in KiCad.
- The simulation uses behavioural models, not manufacturers' SPICE models. It catches wrong
  values, overloaded parts and dead rails, not subtle analog behaviour.
- Imported footprints from project-specific libraries need standard KiCad equivalents.

## Benchmark

`python -m wholeboard.bench` runs fifteen reference prompts end to end and records, for each,
whether it finished, how many rebuilds the AI needed, ERC and DRC findings, unrouted connections,
simulation failures, the board cost, tokens and API cost. Results are kept in
[bench/RESULTS.md](bench/RESULTS.md), so a change to a model, the prompt or a block can be measured.
See [bench/README.md](bench/README.md) for running it with other models and on a schedule.

Which worker and expert pairing to use will be recommended here from those results; no run has been
recorded yet.

## Development

```
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

Code map: `wholeboard/ai` (providers, the design agent and the expert model), `wholeboard/blocks.py`
(building blocks), `wholeboard/power.py`, `wholeboard/simlab.py`, `wholeboard/boxheat.py` (heat in the
enclosure) and `wholeboard/web/simlab.js` (Sim Lab), `wholeboard/impedance.py`, `wholeboard/bringup.py`,
`wholeboard/ibom.py` (assembly page), `wholeboard/fwbuild.py` (PlatformIO), `wholeboard/dxf.py`,
`wholeboard/case.py` (stock boxes), `wholeboard/report.py` (PDF), `wholeboard/kicad` (libraries,
board and schematic writers, kicad-cli, geometry for the viewer), `wholeboard/router.py`
(TraceMaker), `wholeboard/jlc` (parts, quote, stock check, other fabs and suppliers), `wholeboard/mains.py`
(mains spacing), `wholeboard/kicad/handedit.py` (keeping KiCad edits), `wholeboard/kicad/dru.py` (JLCPCB rules),
`wholeboard/kicad/panelize.py` (KiKit), `wholeboard/mcp.py` (MCP server), `wholeboard/pipeline.py` (the build steps),
`wholeboard/server.py` and `wholeboard/web` (API and interface).

## Licence

WholeBoard is free software under the GNU General Public License, version 3 or later. See
[LICENSE](LICENSE).
