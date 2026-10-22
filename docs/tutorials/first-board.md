# Your first board

This lesson builds a small board from one sentence: four LEDs that chase each other, powered from USB-C. It
takes about fifteen minutes, most of it waiting for the router, and costs a few cents of AI time with Claude.

## Before you start

- WholeBoard is installed and `python -m wholeboard.doctor` shows KiCad, TraceMaker and ngspice as found
  ([Install and check the tools](../how-to/install.md)).
- An AI is set in *Settings → AI* (an Anthropic key, an OpenAI or xAI key, or a local server).

## 1. Start WholeBoard and take the tour

Run `./run.sh` (`run.bat` on Windows). The browser opens at <http://127.0.0.1:8770/>.

Press **Take a tour** in the top bar. It walks through a normal workflow and highlights each part of the page:
the chat, the step bar, the visuals, the quote and the enclosure. It is the quickest way to learn where things are.

## 2. Describe the board

Press **+ New board** and type:

> Four LEDs that light one after another, about twice a second, powered from USB-C. Small, two layers.

The designer reads the request and asks only what changes the design. Expect a question or two, such as whether
you want an enclosure and whether the board should screw into it. Answer in the chat; the buttons under a
question are suggestions, and you can type anything.

## 3. Watch the steps

The step bar along the top fills in as the build goes. Click any step to see its visual:

| Step | What to look at |
|---|---|
| Parts & architecture | The designer starts from the `usb_c_power` and `led_indicator` building blocks and searches JLCPCB for the rest |
| Design check | Every footprint exists, every pad is in a net, every part is in stock |
| Schematic & ERC | One sheet per block; KiCad's electrical rules check |
| Simulation | ngspice runs the circuit: the 5 V rail, each LED's current, the timing |
| Placement and routing | TraceMaker's live viewer, then the routed board |
| DRC & copper pour | KiCad's DRC against JLCPCB's rules, trace heating and voltage drop |
| 3D views & fab files | Renders, Gerbers, BOM and CPL |
| JLCPCB quote | Board, assembly, parts and shipping, itemised |

If a step fails, the result goes back to the designer, which fixes the design and builds again. You will see it
say what went wrong and what it changed.

## 4. Look at the simulation on the board

Click the **Simulation** step. Once the board is routed it opens the **Sim Lab**: the finished board in 3D with current flowing along the
tracks and each LED lit as brightly as its simulated current. Click a risk in the list (an LED near its current
limit, say) to fly to it, and move the resistor slider: ngspice runs again in under a second. The design is not
changed; ask the designer in the chat to make a change stick.

## 5. Read the quote

The quote itemises the bare board, assembly, every part at today's LCSC price, and shipping. Look for:

- **Cheaper parts**: drop-in replacements that avoid JLCPCB's extended-part fee. Tick them to apply.
- **Panel options**: for a board this small, a panel is often cheaper.
- **Other fabs**: OSH Park, PCBWay and Aisler for comparison.

## 6. The enclosure

Once the board is clean and quoted, the designer plans a box and FreeCAD builds it. The step shows a 3D preview,
any collisions between parts and the box, how warm the inside gets, and downloads: `base.stl` and `lid.stl` for
printing, STEP files and an editable FreeCAD macro.

## 7. Change something

Type in the chat:

> Make the LEDs blink faster and use green ones.

The designer edits the design and builds a new revision. *History* lists every revision with what changed; tick
two to compare them.

## What next

- [Order the board](../how-to/order.md)
- [Edit the board in KiCad](../how-to/kicad-edits.md): moved parts and hand-routed tracks are kept on the next build.
- [How a build works](../explanation/pipeline.md)
