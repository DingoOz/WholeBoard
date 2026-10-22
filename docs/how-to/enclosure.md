# Design the enclosure

Once the board is clean and quoted, the designer plans an enclosure and FreeCAD builds it. To skip it, say so
in the chat; to use a box you already have instead, see [Fit a board to a case](case-fit.md).

## How it is designed

1. The AI reads the finished board: its size, its mounting holes, and every part with position, estimated height
   and nearest edge.
2. It decides what the box needs and asks you about real choices, such as a screwed or press-fit lid, wall mounting
   or a label. Then it writes an enclosure design: openings for connectors, lid holes for LEDs and buttons, how the
   board is held, vents, rubber-foot pockets, mounting ears and an engraved label.
3. FreeCAD builds the base and lid, then checks every part against them. Collisions go back to the AI, which fixes
   the design and builds again.

Lids can be a press-fit lip, four screws, or **snap-fit**: a deeper skirt with bumps that click into grooves in the
walls, plus a notch in the front wall to lever it off.

Part heights come from the 3D models: KiCad's model for the footprint first, then the model on the part's
LCSC/EasyEDA page. Footprint names are the fallback, and the AI can give datasheet heights.

## Change it

Ask the designer in the chat, or use *Edit enclosure JSON*. If you change the board, the enclosure is rebuilt
around it.

## Heat in the box

The step reports, from the board's simulated heat, how warm the air in the box gets, how hot the lid and walls
are to the touch, and how hot the hottest parts run in the box, plus what lid and side vents would change. If the
box runs warm, the designer is told and adds vents or headroom. See
[Heat on the board and in the box](../explanation/thermal.md).

## Print it

The step shows a 3D preview and the details, including filament, print time and cost from the material, filament
price, printer speed and electricity price in *Settings → JLCPCB → 3D printing*. Downloads:

- `base.stl` and `lid.stl` for printing;
- STEP files;
- the FreeCAD model, and a FreeCAD macro that rebuilds the parts from editable numbers.

Print the base open side up and the lid flat side down. Neither needs supports. Check the openings against your
parts' datasheets before printing.
