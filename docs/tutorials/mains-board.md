# A mains-powered board

This lesson builds a zero-cross detector: a board that plugs into 230 V mains and gives a microcontroller a pulse
each time the mains voltage crosses zero, plus 5 V to run it. Along the way you will see the checks WholeBoard
applies to anything that touches mains.

!!! danger
    Mains can kill. WholeBoard's checks are an aid, not a certification. Do not build or power a mains board
    unless you know how to do it safely, have the design reviewed, and put it in an enclosure that keeps fingers
    out.

## 1. Ask for it

> A zero-cross detector for 230 V mains: screw terminals in, a fuse, a varistor, an HLK-PM01 for 5 V and an
> optocoupler giving a zero-cross pulse. A 3-pin header out with 5 V, ZC and GND.

## 2. What the designer declares

Because the board touches mains, the design has a `mains` section naming the nets that carry the full mains
voltage:

```json
"mains": {"nets": ["AC_L", "AC_N", "AC_LF"], "volts": 230}
```

`AC_LF` is the live after the fuse. `isolated` defaults to `true`: the low-voltage side (the header) is meant to be
safe to touch, so it must be separated from the mains side by reinforced insulation.

If the designer forgets this section but uses nets like `AC_L` together with mains parts (a fuse holder, a
varistor, an AC-DC module), the build is refused until it is added.

## 3. The design check

Before anything is drawn, WholeBoard checks:

- **A fuse** on the mains input (`F1`, the 5×20 mm holder).
- **An isolation barrier**: something the mains side cannot reach through. Here there are two: the HLK-PM01
  supply (`PS1`) and the optocoupler (`U1`). Everything reachable from the mains nets through other parts
  (the zero-cross resistors `R1` and `R2`, the optocoupler's LED) is the *mains side*.

## 4. Spacing

At 230 V, overvoltage category II:

| Between | Rule | Distance |
|---|---|---|
| Mains nets (live and neutral, before and after the fuse) | IPC-2221 B2, outer layers | 2.5 mm |
| The mains side and everything else | IEC 62368-1 reinforced clearance | 3.0 mm |
| The mains side and everything else, along the surface | Reinforced creepage | 5.0 mm |

These go into the board's net classes and `.kicad_dru`, so TraceMaker routes to them and KiCad's DRC checks them.
The [Mains requirements](../reference/mains.md) page has the figures for other voltages.

## 5. The slot

The LTV-814S optocoupler is in an SO-4 package. Its input pins (mains side) and output pins (low-voltage side)
are about 4.5 mm apart along the board: short of the 5 mm creepage. WholeBoard cuts a 1 mm milled slot under the
part, between its two rows of pins, and the log says so. Creepage is measured along the surface, so the path now
goes round the slot: 6.4 mm.

## 6. After DRC: the mains check

After the copper pour and DRC, WholeBoard measures every pair of items across the barrier itself (pads with their
real shapes, tracks, vias, zones), including the path round the slot. The **Board checks** card lists each pair
with its clearance and creepage against the requirement. Any failure stops the build and goes back to the designer.

## 7. Before you order

- Check the fuse rating and the varistor's voltage against your mains.
- Keep the board in an enclosure; the enclosure step can add the openings for the terminal block.
- Have someone qualified review it.

See [Mains isolation](../explanation/mains.md) for how the mains side is found and measured.
