# 7. A worker model and an expert model

**Status:** accepted, October 2026

## Context

Designing a board takes many AI turns: searching parts, writing the design, reading build errors and fixing
them. Running the strongest model for all of it is expensive; running a small or local model for all of it gives
worse architecture choices and gets stuck on hard build failures.

## Decision

An optional expert model is called only at the points where judgement matters most: planning the architecture
once the requirements are clear, reviewing each clean build, after three failed builds in a row, and when the
worker asks (`consult_expert`). The worker does every other turn. Usage is counted per model.

## Consequences

- A cheap or local worker with a strong expert costs much less than the expert alone.
- The best pairing is an empirical question, which is what the [benchmark](../how-to/benchmark.md) is for. No
  pairing is recommended until it has been run.
