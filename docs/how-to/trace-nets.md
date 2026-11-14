# Trace a net through the schematic

Following one signal through a large schematic means finding every label, sheet pin and part pin that carries it,
often on several sheets. The schematic view does that for you.

## Pick what to trace

- **Click the schematic**: a wire, a label, a pin or a power symbol traces its net; the body of a part traces the
  part. Click a bus or a bus label again to step through its members. Click a sheet symbol to open that sheet.
- **Type it**: the box above the schematic takes a net name (`+3V3`, `SDA`, `D3`) or a part reference (`U1`).
- **From the design check**: net names and part references in the *Check* step's tables are links.
- **From a finding**: an ERC error, a review note or any other finding that names a net or part has a *Trace*
  button beside each one in its dialog.

## Follow it

The traced net's wires, labels and pins are drawn over the schematic, and everything else is dimmed.

- The counter reads like *3 of 6, 2 on the Power sheet*: the place you are on, how many there are, and how many
  are on this sheet. Each sheet tab shows how many places it holds.
- **N** and **P** (or the **‹** and **›** buttons) go to the next and previous place, changing sheet when they
  need to. **Esc** stops tracing.
- Arrows at the edges of the view point to places on this sheet that are off screen. Click one to go there.
- The minimap in the corner shows the whole sheet, a dot for each place and a box for what you can see. Drag in it
  to move the view.

A place counts as seen once it has been on screen at a size you can read. When you have seen every place, the
counter says *traced ✓*.

## Keep a review checklist

*Checklist* lists every net on the schematic and ticks each one you have traced to the end. Untick a net to review it
again, or click its name to trace it. The checklist is kept in your browser, per board.

## Imported projects

When you import a KiCad project, its own schematic is shown on the *Request* step, sheet by sheet, before
WholeBoard draws its own. Nets are traced there in the same way, through wires, buses, power symbols and
hierarchical sheets, using the net names the import gave them.
