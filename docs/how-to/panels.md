# Make a panel

A panel puts several copies of the board on one sheet, with rails for the assembly machine. JLCPCB can make the
panel for you from single-board files, or you can draw it yourself with KiKit and get panel Gerbers, BOM and CPL.

## Install KiKit

```
.venv/bin/python -m wholeboard.doctor --install kikit
```

KiKit runs on KiCad's own Python.

## Make the files

Either:

- in the quote, choose a panel (columns × rows) and press *Make the N × M panel files*; or
- put it in the design: `"board": {"panel": {"nx": 2, "ny": 2}}`.

KiKit draws:

- **V-cuts** for boards with square corners, **tabs with mouse bites** for rounded ones;
- **5 mm rails** top and bottom, with three tooling holes and three fiducials.

You get panel Gerbers, a panel BOM and a panel CPL with each copy's parts renamed (`R1_0`, `R1_1` …), and the
panel in the board view. Milled slots (for example [mains isolation slots](../tutorials/mains-board.md#5-the-slot))
are kept.

## Fiducials on a single board

Parts at 0.5 mm pitch or finer need fiducials for the assembly machine. Without a panel, set
`"board": {"fiducials": true}` to put three in the corners. WholeBoard warns when a board needs them and has none.

## Parts on both sides

With `"board": {"double_sided": true}`, TraceMaker's placer may put parts on the bottom, and a part's
`placement.side` pins it to one side. Bottom-side parts are flipped exactly as KiCad flips them, and the quote adds
JLCPCB's second-side setup and stencil (standard assembly only).
