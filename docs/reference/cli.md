# Command line

On Windows, use `.venv\Scripts\python` in place of `.venv/bin/python`.

## `wholeboard`

Starts the server and opens the browser. Also `./run.sh`, `run.bat` or `.venv/bin/python -m wholeboard`.

| Option | |
|---|---|
| `--host ADDRESS` | Address to listen on (default `127.0.0.1`; `0.0.0.0` for other computers) |
| `--port N` | Port (default 8770, or `port` in the settings) |
| `--no-browser` | Do not open a browser window |
| `--set-password` | Ask for a password that other computers on the network must enter |
| `--clear-password` | Remove the network password |

## `python -m wholeboard.doctor`

Lists the tools WholeBoard uses, where they were found and their versions.

| Option | |
|---|---|
| `--install [WHAT ...]` | Install missing tools: `tracemaker`, `freecad`, `ngspice`, `libs`, `platformio`, `kikit`. With no names: all but `platformio` and `kikit` |
| `--force` | Reinstall even if found |

## `python -m wholeboard.mcp`

The MCP server, over stdio. No options. See [MCP tools](mcp-tools.md).

## `python -m wholeboard.bench`

The benchmark. See [Run the benchmark](../how-to/benchmark.md).

| Option | |
|---|---|
| `--only ID,ID` | Only these prompts from `bench/prompts.json` |
| `--worker PROVIDER:MODEL` | The worker model, e.g. `anthropic:claude-sonnet-5-5` or `openai:qwen3` |
| `--base-url URL` | The OpenAI-compatible server for an `openai` worker |
| `--expert MODEL` | The expert model (Claude), or `none` to turn the expert off |
| `--timeout MINUTES` | Per prompt (default 45) |
| `--label NAME` | Name for this run in the table |
| `--table` | Only rewrite `bench/RESULTS.md` |

## Environment

| Variable | |
|---|---|
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY` | Used when no key is set in *Settings* |
| `WHOLEBOARD_HOME` | Where settings and caches live (default `~/.wholeboard`) |
