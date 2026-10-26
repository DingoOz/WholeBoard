# Use the checks from other agents (MCP)

`python -m wholeboard.mcp` is a [Model Context Protocol](https://modelcontextprotocol.io/) server over stdio. It
gives any agent (Claude Code, Codex and others) WholeBoard's checks on any KiCad project, designed by WholeBoard or
not: simulation, power budget, impedance, board checks, a JLCPCB quote, a stock check and a bring-up guide.
The full list is in [MCP tools](../reference/mcp-tools.md).

The same server also lets Claude Code design whole boards in WholeBoard: see [Design with Claude Code](claude-code.md).

## Add it to Claude Code

```
claude mcp add wholeboard -- /path/to/WholeBoard/.venv/bin/python -m wholeboard.mcp
```

## Add it to another client

Most clients take a JSON entry like this:

```json
{
  "mcpServers": {
    "wholeboard": {
      "command": "/path/to/WholeBoard/.venv/bin/python",
      "args": ["-m", "wholeboard.mcp"]
    }
  }
}
```

## Use it

Each tool takes `project`: a KiCad project folder, or a `.kicad_pro`, `.kicad_sch` or `.kicad_pcb` in it. For
example, ask your agent:

> Run WholeBoard's board checks on ~/kicad/mains-dimmer with mains nets AC_L and AC_N at 230 V.

The project is read once and cached until its files change. Working files go in `~/.wholeboard/mcp/`. The
tools need the same KiCad and ngspice as WholeBoard itself; they do not need an AI key.
