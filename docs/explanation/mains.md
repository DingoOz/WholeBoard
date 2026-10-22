# Mains isolation

A board that touches mains has two jobs: keep live and neutral from arcing to each other, and keep anything a
person can touch from becoming live. WholeBoard checks both, from the design and from the finished copper.

## Finding the mains side

The design names the nets that carry the full mains voltage (`mains.nets`). From there WholeBoard floods outwards:
any part on a mains net that is **not** an isolation barrier puts its other nets on the mains side too. A resistor
from live, the optocoupler's LED behind it, the input of a supply module: all mains side.

The flood stops at barrier parts: isolated supply modules, transformers, optocouplers, digital isolators and
relays (recognised by name, see [Mains requirements](../reference/mains.md#how-parts-are-recognised)), or any part
marked `"barrier": true`, such as a Y capacitor. Each barrier part has pins on both sides; those are the places
where the two sides come closest.

## Two kinds of spacing

| Between | Why | Rule |
|---|---|---|
| Mains nets (live, neutral, before and after the fuse) | So they do not arc to each other. A fault here blows the fuse | IPC-2221 for the peak voltage (functional insulation) |
| The mains side and everything else | So a single fault cannot make the low-voltage side live. A fault here can kill | IEC 62368-1 reinforced insulation: clearance and creepage |

**Clearance** is the shortest distance through air. **Creepage** is the shortest distance along the board's
surface, which matters because dirt and moisture on the surface conduct. Reinforced creepage at 250 V is 5 mm:
more than many parts' pin spacing.

## Slots

Creepage follows the surface, so cutting through the board makes it longer: the path has to go round. When a
barrier part's mains pins and low-voltage pins are closer than the creepage, WholeBoard cuts a 1 mm milled slot
between them (unless the part says `"slot": false`). The creepage check then measures round it: a shortest path
through the corners of every slot in the way.

## Measuring

After DRC, every copper item on the mains side (pads with their real shapes, tracks, arcs, vias, zones) is
measured against every item on the other side: clearance as the straight-line gap, and creepage on the outer
layers as the shortest path round any slots. Each pair that fails is listed with the two items, the distance and
the requirement, and the build stops.

Spacing between mains nets is checked only between declared mains nets
([decision 2](../decisions/0002-functional-spacing-between-declared-mains-nets.md)).

!!! danger
    This is an aid, not a certification. It does not check component ratings, the enclosure, earthing,
    flammability or the standards your product must meet.
