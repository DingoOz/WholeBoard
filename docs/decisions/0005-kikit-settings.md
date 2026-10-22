# 5. KiKit settings: mill radius, V-cut layer, copy numbering

**Status:** accepted, October 2026

## Context

Panels are drawn with KiKit. Three of its defaults broke WholeBoard boards:

- KiKit rounds inside corners to the mill radius. With its default it closed up the 1 mm mains isolation slots.
- V-cuts drawn on `Edge.Cuts` would be milled through, not scored.
- Copies' references are renamed with `{n}`, which counts from 0, so the panel BOM and CPL must match.

## Decision

- `millradius` is 0.5 mm, half the narrowest slot.
- V-cuts go on `Cmts.User`, which is exported with the panel Gerbers for JLCPCB.
- Copies are named `{orig}_{n}` (`R1_0`, `R1_1` …), and `panelize.copies()` counts from 0 to match.
- Rails are 5 mm top and bottom, with three tooling holes (1.152 mm) and three fiducials. V-cuts for square
  corners; 3 mm tabs with mouse bites for rounded ones.

## Consequences

- Slots survive panelisation, so mains boards can be panelled.
- KiKit runs on KiCad's Python (installed with `uv --target` into `~/.wholeboard/kikit-py`), so it works with the
  Docker KiCad image, which has no `pip`.
