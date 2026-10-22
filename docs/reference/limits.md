# Limits

- 1, 2, 4 or 6 copper layers. Bottom-side assembly needs JLCPCB's standard assembly.
- The schematic is generated from the netlist: correct and usable in KiCad, with a sheet per block and parts
  grouped by signal flow, but joined by net labels rather than wires drawn by hand.
- The mains checks cover spacing on the board, the fuse and the barrier. They are not a safety certification:
  have a mains design reviewed.
- Autorouting quality depends on TraceMaker; dense BGA designs may need manual work in KiCad.
- The simulation uses behavioural models, not manufacturers' SPICE models. It catches wrong values, overloaded
  parts and dead rails, not subtle analog behaviour.
- The heat models are good for hot spots and comparisons, and to a few degrees for small boxes in still air. They
  are not CFD or a thermal camera.
- Controlled-impedance widths use approximate formulas; check them in JLCPCB's impedance calculator.
- Imported footprints from project-specific libraries need standard KiCad equivalents.
- JLCPCB's board, assembly and shipping prices are estimates; the cart is the real price.
