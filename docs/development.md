# Development

## Set up

```
.venv/bin/pip install -e ".[dev,docs]"
.venv/bin/python -m pytest -q
```


## The documentation

```
.venv/bin/mkdocs serve          # http://127.0.0.1:8000, reloads as you edit
.venv/bin/mkdocs build --strict # what CI runs
```

- Pages are Markdown in `docs/`, organised by [Diátaxis](https://diataxis.fr/): a tutorial teaches, a how-to guide
  does one job, reference describes, explanation gives the why. Put a new page in the section it belongs to and add
  it to `nav` in `mkdocs.yml`.
- Pages built from the code (blocks, profiles, mains, MCP tools, limits, settings, the spec, the Python API) are
  written by `docs/gen.py`. Change the code, not the page ([decision 1](decisions/0001-docs-generated-from-code.md)).
- When you take a figure from a standard or a price from a supplier, add it to [Standards and sources](standards.md)
  with the date.
- When you make a choice someone would otherwise undo, write a [decision record](decisions/index.md).
- Add a line to `CHANGELOG.md` under *Unreleased* for anything a user would notice.

The site is published to GitHub Pages by `.github/workflows/docs.yml` on every push to `main`.

## Code map

| Where | What |
|---|---|
| `wholeboard/ai/` | Providers, the designer agent, the expert model |
| `wholeboard/pipeline.py` | The build steps |
| `wholeboard/design.py` | Design validation and normalisation |
| `wholeboard/blocks.py` | Building blocks |
| `wholeboard/sim.py`, `ngspice.py`, `ngspice_shared.py`, `simlab.py` | Simulation and the Sim Lab |
| `wholeboard/power.py` | Power budget |
| `wholeboard/checks.py` | Trace heating, voltage drop, decoupling |
| `wholeboard/impedance.py` | Controlled impedance and interface profiles |
| `wholeboard/mains.py` | Mains spacing, slots and rules |
| `wholeboard/boxheat.py` | Heat in the enclosure |
| `wholeboard/enclosure.py`, `freecad_enclosure.py`, `case.py`, `dxf.py` | Enclosures, stock cases, DXF outlines |
| `wholeboard/kicad/` | Libraries, board and schematic writers, `kicad-cli`, geometry, rules file, hand edits, KiKit |
| `wholeboard/router.py` | TraceMaker |
| `wholeboard/jlc/` | Parts, quote, stock check, savings, panels, other fabs, Mouser and DigiKey |
| `wholeboard/bringup.py`, `ibom.py`, `firmware.py`, `fwbuild.py`, `report.py` | Bring-up guide, assembly page, firmware, PDF report |
| `wholeboard/revisions.py` | History and comparison |
| `wholeboard/mcp.py` | The MCP server |
| `wholeboard/bench.py`, `bench/` | The benchmark |
| `wholeboard/server.py`, `wholeboard/web/` | API and interface |
| `wholeboard/doctor.py` | Tool checks and installers |

Module-level detail is in the [Python API](api/index.md).
