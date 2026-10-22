# Files and folders

## Projects

`~/WholeBoardProjects/<date>-<name>/` (the folder is set by `projects_dir` in [Settings](settings.md)):

| Path | |
|---|---|
| `revN/` | One folder per build |
| `revN/*.kicad_pro`, `.kicad_sch`, `.kicad_pcb`, `.kicad_dru` | The KiCad project. Open it in KiCad; [your edits are kept](../how-to/kicad-edits.md) |
| `revN/fab/` | Gerber zip, BOM and CPL for JLCPCB; panel files when made |
| `revN/sim/` | The SPICE netlist and plots |
| `revN/firmware/` | The PlatformIO project |
| `revN/quote.json` | The quote |
| `revN/summary.json` | The *History* entry |
| `exports/` | The zipped KiCad project, firmware and bring-up guide |

## WholeBoard's own files

| Path | |
|---|---|
| `~/.wholeboard/config.json` | Settings and API keys (readable by your user only on Linux and macOS) |
| `~/.wholeboard/blocks/` | [Your own building blocks](../how-to/add-block.md) |
| `~/.wholeboard/models3d/` | Downloaded 3D part models |
| `~/.wholeboard/tools/` | Tools the installer fetched (FreeCAD AppImage) |
| `~/.wholeboard/kikit-py/` | KiKit, for KiCad's Python |
| `~/.wholeboard/mcp/` | The MCP server's working copies |
| `~/.wholeboard/` | Also library copies and the parts cache |

Set `WHOLEBOARD_HOME` to move `~/.wholeboard`.

## In the repository

| Path | |
|---|---|
| `wholeboard/jlc/pricing.json` | JLCPCB's board, assembly and shipping prices (estimates; edit when they change) |
| `wholeboard/jlc/capabilities.json` | JLCPCB's manufacturing limits for the rules file |
| `bench/prompts.json`, `bench/results/`, `bench/RESULTS.md` | The benchmark |
