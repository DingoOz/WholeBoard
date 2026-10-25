# Design with Claude Code

Claude Code can be WholeBoard's designer. You describe the board in a Claude Code session. Claude Code picks the
parts, writes the design and has WholeBoard build it, and the board appears in WholeBoard as it is built: the
schematic, simulation, renders, quote and enclosure.

Claude Code runs on whatever it is signed in with, such as your Claude Pro or Max plan, so WholeBoard needs no AI
key for this. WholeBoard never sees your Claude sign-in: Claude Code calls WholeBoard's tools over
[MCP](mcp.md), the same way it uses any other MCP server.

## Set it up

WholeBoard shows these steps for your computer: click *Design from Claude Code instead* under the description box
on the home page, or *Set up* next to Claude Code in *Workbench setup*. It shows the exact line to add WholeBoard,
with this computer's paths, and can run it for you (*Add it for me*, only on the computer running WholeBoard). It
also shows whether Claude Code is installed and already has WholeBoard.

1. Start WholeBoard (`./run.sh`, or `run.bat` on Windows). Claude Code's tools reach it at
   <http://127.0.0.1:8770/>. If it runs on another port, set `WHOLEBOARD_URL` (for example
   `http://127.0.0.1:8799`) in the server's environment.
2. Add WholeBoard to Claude Code once, for every project:

    ```
    claude mcp add --scope user wholeboard -- /path/to/WholeBoard/.venv/bin/python -m wholeboard.mcp
    ```

    On Windows the Python is `\path\to\WholeBoard\.venv\Scripts\python.exe`. Leave out `--scope user` to add it
    to one project only.

## Design a board

Ask Claude Code, in any session:

> Design a board with WholeBoard: four LEDs that light one after another, about twice a second, powered from USB-C.

Claude Code reads the designer's guide, starts the board and gives you its link. Open the link to watch the board
being built. Answer Claude Code's questions in Claude Code; it records your answers on the board's *Requirements*
step. A build takes a few minutes. Claude Code waits for it, then looks at the renders to review the layout.

To change a board later:

> Open my NE555 blinker in WholeBoard and make the LEDs green.

## While it works

- The board's header shows **Claude Code** where the token count usually is. Claude Code's usage counts against
  your Claude plan.
- **Stop** in WholeBoard stops the current build, and Claude Code is told you stopped it.
- Messages you type in WholeBoard's chat reach Claude Code with its next step.
- If Claude Code has not touched the board for 30 minutes, a message in WholeBoard's chat goes to WholeBoard's own
  designer (the AI in *Settings*) instead, which carries on from the current design.
- WholeBoard's chat shows your request, your answers, the tools Claude Code used and its final summary. The rest
  of the conversation stays in Claude Code.

## What is different from WholeBoard's own designer

The steps, checks and files are the same. Claude Code asks you its questions itself, so it has no `ask_user` tool,
and the expert model in *Settings* is not used: there is no second opinion on the plan or the review. The tools are
listed in [MCP tools](../reference/mcp-tools.md#design-a-board).

The tools only work on the computer running WholeBoard: they call it on the loopback address, which needs no
password.
