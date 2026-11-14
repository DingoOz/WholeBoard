# WholeBoard roadmap

Proposed features that are not built yet. Each item has a short design note and a rough size:
**S** (a day or two), **M** (about a week), **L** (several weeks). Finished items move to
CHANGELOG.md; bugs and smaller gaps are tracked in BUGS.md.

## Reviewing a design

### Net tracing in Live view and on bus aliases (S)

Net tracing works in the Schematic step and on imported projects' schematics. Two gaps remain:

- The schematic tile in Live view is still a plain picture; give it the same picking and highlighting.
- Bus members come from `NAME[a..b]` and `{A B}` label text only. Read KiCad's bus aliases from the
  schematic too.

## Design capability

### Antenna models for the Sim Lab (M)

The antenna simulation puts a generic inverted-F, about a quarter wave long, in place of each module's own
antenna, so its results are a comparison between layouts and boxes, not a prediction.

- Use the maker's reference geometry where it is published (chip-antenna evaluation boards, Espressif's and
  Nordic's reference PCB antennas), and tune the stand-in to resonate in the band on the bare board before
  adding the box.
- Show the radiation pattern on the Sim Lab's 3D board, not only as two polar cuts.
