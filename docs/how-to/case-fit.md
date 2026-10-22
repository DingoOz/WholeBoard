# Fit a board to a case

## A board outline from a DXF

*Board outline (DXF)* reads a drawing from your CAD program: lines, arcs, circles and polylines, in millimetres or
inches. The largest closed shape becomes the board edge, and the circles inside it become mounting holes. The
designer then builds the board to that shape (`board.outline`).

## A box you already have

Instead of designing an enclosure, the design can name an existing box in `board.case`, with its inside size,
wall, floor and standoff height:

```json
"case": {"name": "Hammond 1591XXSSBK", "inner_mm": [80, 50, 26], "standoff_mm": 5,
         "wall_mm": 2.5, "floor_mm": 2.5, "material": "ABS"}
```

WholeBoard checks the board and its tallest parts fit, then draws 1:1 drilling templates (SVG, with a 50 mm scale
bar) for every wall with a connector and for lid holes over LEDs and buttons. Print them at 100 % and check the
scale bar before drilling.

The `material` is used for the [heat in the box](../explanation/thermal.md). The full format is in the
[Design spec](../reference/spec.md).
