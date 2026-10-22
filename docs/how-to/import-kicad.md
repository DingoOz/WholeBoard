# Import a KiCad project

1. On the home page, press *Import KiCad project…*.
2. Choose a `.kicad_sch` (and its `.kicad_pcb`, if you have one), or a zip of the project folder.

KiCad reads the schematic's netlist: parts, values, footprints, LCSC numbers (from an `LCSC`, `LCSC Part` or
`JLCPCB Part` field) and nets. The board, if given, supplies the outline, layer count, mounting holes and part
positions.

The AI then checks the design, finds JLCPCB parts for anything without an LCSC number, swaps footprints that are
not in KiCad's standard libraries, and builds it. With no AI configured, the import is built as it is.

!!! tip
    To run WholeBoard's checks on a KiCad project without importing it, use the [MCP server](mcp.md).
