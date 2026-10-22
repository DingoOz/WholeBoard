# The two AI models

*Settings → AI → Expert model* adds a second, stronger model.

The **worker** does every turn: part searches, writing the design, reading build results and fixing them. Most of
the tokens in a board are spent here, on routine work.

The **expert** is called only when its judgement is worth the price:

| When | What it is asked |
|---|---|
| The requirements are clear | To plan the architecture: which blocks, which parts, which layer count |
| A build is clean | To review it before ordering, from the build result and the renders |
| The worker has failed three times in a row | What is going wrong |
| The worker asks (`consult_expert`) | Its question |

A cheap or local worker with Claude Opus as the expert costs much less than running the expert on every turn,
and the plan and review catch the mistakes a smaller model makes. The token count in the header splits usage by
model.

Which pairing works best is a question for the [benchmark](../how-to/benchmark.md). No recommendation is made
until it has been run.
