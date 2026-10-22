# 3. A surface clearance rule stands in for creepage

**Status:** accepted, October 2026

## Context

KiCad 10's DRC has a creepage constraint, so the first version wrote one into the `.kicad_dru`. The zone filler
ignores it: the ground pour filled up to the 3 mm clearance from the mains side, and the DRC then reported creepage
violations against the pour on every mains board. TraceMaker routes to clearance, not creepage, so tracks did the
same.

## Decision

Two rules are written for the mains side:

- **Reinforced isolation**: clearance and creepage, on all layers, as the standards say.
- **Reinforced isolation, surface copper**: on the outer layers, a *clearance* equal to the creepage distance,
  except pad to pad.

Creepage is also measured by WholeBoard itself after DRC (`mains.check`), along the surface and round slots.

## Consequences

- Pours and tracks keep the creepage distance from the mains side, so DRC passes and the board is right.
- On outer layers, copper is held further from the mains side than clearance alone requires, even where a slot
  would have allowed closer. That costs a little board area, on the safe side.
- Pad-to-pad gaps across a barrier part are left to the slot and to WholeBoard's own creepage check.
