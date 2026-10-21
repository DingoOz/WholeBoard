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

| Step | What happens | Visual |
|---|---|---|
| Requirements | The AI reads your request and asks questions in the chat | Conversation |
| Parts & architecture | Searches JLCPCB parts, KiCad footprints and symbols | Tool activity in the chat |
| Design check | Footprints exist, pad names are right, nets are sane, parts are in stock | Parts and nets tables |
| Schematic & ERC | A KiCad schematic, one symbol per part, nets joined by labels, checked by KiCad's electrical rules check | Zoomable schematic, ERC findings |
| Simulation | ngspice runs the circuit: rail voltages, currents, part ratings, heat, battery life, oscillator frequency, filter response | Plots, ratings table, the SPICE netlist |
| Board & footprints | Outline, mounting holes, footprints with nets | Board view |
| Placement | `tracemaker-place` places the parts | Board view with air wires |
| Routing | `tracemaker route` routes every connection | TraceMaker's live viewer, then the routed board |
| DRC & copper pour | Ground pour, zone fill, KiCad DRC, then trace heating, voltage drop and decoupling-capacitor distances | Final board, DRC summary, board checks |
| 3D views & fab files | 3D renders with real part models, Gerbers, drill, BOM and CPL (rotations corrected for JLCPCB), starter firmware for microcontroller boards | Renders, downloads, pin map |
| JLCPCB quote | PCB + assembly + components + shipping, cheaper drop-in parts, panel options | Itemised quote you can re-run with other options |
| AI visual review | The AI looks at the renders like a reviewer and fixes real problems | Review notes |
| 3D-printed enclosure | The AI designs a box and FreeCAD builds and checks it | 3D preview, collision report, STL/STEP/FreeCAD downloads |

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
- **Board checks.** Track temperature rise (IPC-2221) for the simulated currents, voltage drop
  along power nets, and decoupling capacitors that sit too far from their ICs.
- **CPL rotations.** JLCPCB draws many packages turned from KiCad's zero angle. The CPL file is
  corrected (SOT-23, SOIC, QFN and so on), and a component's `cpl` entry can override it.
- **AI visual review.** After a clean build, the AI is shown the 3D renders and looks for parts
  off the board, blocked connectors and hidden LEDs. Models that cannot see images skip this
  step, and *Settings → AI → Vision* turns it off.

## Starting from an existing KiCad project

*Import KiCad project…* on the home page takes a `.kicad_sch` (and its `.kicad_pcb`), or a zip
of the project folder. KiCad reads the schematic's netlist: parts, values, footprints, LCSC numbers
(from an `LCSC`, `LCSC Part` or `JLCPCB Part` field) and nets. The board, if given, supplies the
outline, layer count, mounting holes and part positions. The AI then checks the design, finds
JLCPCB parts for anything without an LCSC number, swaps footprints that are not in KiCad's
standard libraries, and builds it. With no AI configured, the import is built as it is.

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
  price it. JLCPCB builds it from the single-board files when you pick *Panel by JLCPCB*.
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

- Single-sided SMT assembly on the top layer; 1, 2, 4 or 6 copper layers.
- The schematic is generated from the netlist: correct and usable in KiCad, but laid out as a
  grid of symbols joined by net labels rather than drawn by hand.
- Autorouting quality depends on TraceMaker; dense BGA designs may need manual work in KiCad.
- The simulation uses behavioural models, not manufacturers' SPICE models. It catches wrong
  values, overloaded parts and dead rails, not subtle analog behaviour.
- Imported footprints from project-specific libraries need standard KiCad equivalents.

## Development

```
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

Code map: `wholeboard/ai` (providers and the design agent), `wholeboard/kicad` (libraries,
board and schematic writers, kicad-cli, geometry for the viewer), `wholeboard/router.py`
(TraceMaker), `wholeboard/jlc` (parts and quote), `wholeboard/pipeline.py` (the build steps),
`wholeboard/server.py` and `wholeboard/web` (API and interface).

## Licence

WholeBoard is free software under the GNU General Public License, version 3 or later. See
[LICENSE](LICENSE).
