# Build steps

| Step | What happens | Visual |
|---|---|---|
| Requirements | The AI reads your request and asks questions in the chat | Conversation |
| Parts & architecture | Starts from proven building blocks, searches JLCPCB parts, KiCad footprints and symbols | Tool activity in the chat |
| Design check | Footprints exist, pad names are right, nets are sane, parts are in stock, mains has a fuse and a barrier | Parts and nets tables |
| Schematic & ERC | A hierarchical KiCad schematic, one sheet per building block, laid out left to right in signal order, checked by KiCad's electrical rules check | Zoomable schematic with a tab per sheet, ERC findings |
| Simulation | ngspice runs the circuit: rail voltages, currents, part ratings, heat, battery life, oscillator frequency, filter response | Plots, ratings table, the SPICE netlist |
| Power budget | Current per supply and rail, regulator headroom and heat, battery life on a duty cycle | Budget meters, thermal table |
| Sim Lab | The simulation on the finished 3D board: current flow, LED brightness, a heat map, every risk with sliders that re-run ngspice, the board in its enclosure | Interactive 3D view, guided tour, recorded video |
| Board & footprints | Outline, mounting holes, footprints with nets | Board view |
| Placement | `tracemaker-place` places the parts, on both sides when the board allows it; isolation slots are cut under mains barrier parts | Board view with air wires |
| Routing | `tracemaker route` routes every connection | TraceMaker's live viewer, then the routed board |
| DRC & copper pour | Ground pour, zone fill, KiCad DRC against JLCPCB's rules file, then trace heating, voltage drop, decoupling-capacitor distances, interface routing limits and mains creepage | Final board, DRC summary, board checks |
| 3D views & fab files | 3D renders with real part models, Gerbers, drill, BOM and CPL (rotations corrected for JLCPCB), an interactive assembly page, a bring-up guide, starter firmware, panel files | Renders, downloads, pin map, bring-up checklist, panel |
| JLCPCB quote | PCB + assembly + components + shipping, cheaper drop-in parts, panel options, Mouser and DigiKey for parts JLCPCB cannot fit, OSH Park, PCBWay and Aisler for comparison, a live stock check | Itemised quote you can re-run with other options |
| AI visual review | The AI looks at the renders like a reviewer and fixes real problems | Review notes |
| 3D-printed enclosure | The AI designs a box and FreeCAD builds and checks it, or fits the board to a box you already have; heat in the box | 3D preview, collision report, STL/STEP/FreeCAD downloads, drilling templates |
| Summary | Gathers the finished board on one page | Render, price, a tile per check, the designer's list of things to check before ordering, files |
| Project report | One PDF with the renders, quote, BOM, simulation, power budget, checks, bring-up steps and schematic | Download |

If a step fails (a pad name is wrong, routing leaves connections open, DRC finds errors), the result goes back to
the AI, which fixes the design and builds it again. You can also edit the design JSON yourself and rebuild without
the AI.

The header shows how many tokens the AI has used on the board and what that cost at list prices. For
OpenAI-compatible services, set their price per million tokens in *Settings*. Local models count as free.

Each build is kept in its own `revN` folder. *History* lists them with a render, the board size, the price and
what changed from the build before. Tick two to compare parts, values, footprints, LCSC numbers, nets, board
settings and rules. *Restore* rebuilds an old design as a new revision. Nothing is deleted.

See [How a build works](../explanation/pipeline.md) for the order and what stops a build.
