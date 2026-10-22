# Run the benchmark

`python -m wholeboard.bench` runs fifteen reference prompts end to end, from a 555 blinker to an ESP32-S3 sensor
board, and records how each went. Use it after changing a model, the system prompt or a block, and compare with
the runs before.

```
.venv/bin/python -m wholeboard.bench                      # every prompt, with the AI set in Settings
.venv/bin/python -m wholeboard.bench --only blink555,can_node
.venv/bin/python -m wholeboard.bench --worker anthropic:claude-sonnet-5-5 --expert claude-opus-5-5
.venv/bin/python -m wholeboard.bench --worker openai:qwen3-coder --base-url http://localhost:8080/v1 --expert none
.venv/bin/python -m wholeboard.bench --table               # rewrite bench/RESULTS.md only
```

!!! warning "It costs money"
    A full run makes fifteen boards and calls the AI many times. With Claude Opus as the worker, expect a few
    dollars per prompt. Try two or three prompts first with `--only`.

## What it records

For every prompt:

- **Completed**: at least one clean build (DRC clean, everything routed, quoted).
- **Rebuilds**: builds after the first; each is the AI fixing something.
- **ERC, DRC, unrouted**: what was left on the last clean build (or the last build, if none was clean).
- **Simulation failures**: builds the simulation stopped.
- **Board cost**: the JLCPCB quote total.
- **Tokens and API cost**: from the project's usage counter, at list prices.

Each prompt runs unattended: the designer is told not to ask questions, and gets "use your best judgement" if it
asks anyway. A prompt has 45 minutes (`--timeout`). Results go to `bench/results/<date>-<models>.json` and the
table in `bench/RESULTS.md`. The projects are kept under `~/WholeBoardProjects/bench/`.

## On a schedule

The benchmark needs KiCad, TraceMaker, ngspice and an AI key, so it runs on a machine that has them, not in CI.
A weekly cron job:

```
0 3 * * 0  cd /path/to/WholeBoard && .venv/bin/python -m wholeboard.bench >> bench/cron.log 2>&1
```

Commit `bench/results/` and `bench/RESULTS.md` after a run you want to keep.
