# 4. The project and rules files go beside every board TraceMaker sees

**Status:** accepted, October 2026

## Context

TraceMaker reads a board's net classes from the sibling `.kicad_pro` and custom rules from the sibling
`.kicad_dru`. The pipeline hands TraceMaker intermediate copies of the board (after placement, before routing),
in their own files. Only the final board had the project and rules files beside it, so TraceMaker routed with the
default rules and only the final DRC saw JLCPCB's limits and the mains spacing.

## Decision

Every time the pipeline writes a board for TraceMaker, it copies the `.kicad_pro` and `.kicad_dru` beside it
under the matching name (`router._copy_project`).

## Consequences

- Routing respects the same rules DRC checks, so rules failures after routing are rare.
- A new rule only has to be written once, into KiCad's files ([How rules reach the router](../explanation/rules.md)).
